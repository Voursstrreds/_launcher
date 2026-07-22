#include "dbus_tracker.h"
#include "constant_limits.h"

#include <linux/limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <limits.h>
#include <time.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <signal.h>

/* ---------------------------------------------------------------------------
 * Instance operation — passed to manager_operate_instance to select which
 * sd-bus action is performed on a given instance.
 * --------------------------------------------------------------------------- */

enum instance_operation {
    INSTANCE_OP_ENABLE,       /* Enable the unit file.                        */
    INSTANCE_OP_DISABLE,      /* Disable the unit file.                       */
    INSTANCE_OP_START,        /* Start the unit.                              */
    INSTANCE_OP_STOP,         /* Stop the unit.                               */
    INSTANCE_OP_RESTART,      /* Restart the unit.                            */
    INSTANCE_OP_RELOAD,       /* Reload the unit.                             */
    INSTANCE_OP_GET_UNIT,     /* Get the D-Bus object path via GetUnit.       */
    INSTANCE_OP_CHECK_STATE   /* Read ActiveState, SubState, ExecMainPID.     */
};

/* ---------------------------------------------------------------------------
 * Behavior enums. failure_behavior is instance-level (one per unit, the
 * whole unit either aborts or is restarted). mapping_behavior is per-
 * mapping (one entry per registered mapping on each instance), populated
 * by the manifest parser. Each instance carries five parallel arrays of
 * length mapping_count: after_lengths, after_list, before_lengths,
 * before_list, and mapping_behaviors.
 * --------------------------------------------------------------------------- */

enum failure_behavior {
    FAILURE_BEHAVIOR_ABORT,   /* On peer failure: abort this instance.        */
    FAILURE_BEHAVIOR_RESTART  /* On peer failure: restart this instance.      */
};

enum mapping_behavior {
    MAPPING_BEHAVIOR_IGNORE,  /* Failure does not propagate through mapping.  */
    MAPPING_BEHAVIOR_CASCADE  /* Failure cascades to other mapping members.   */
};

/* Top-level step-selection mode. Populated by manager_read_config from
 * PROGRAM_MODE; main() gates the spine on this. Five values name the five
 * compositions of the three primitives generate / start / monitor:
 *   GENERATE                  — dump manifest + unit files, exit.
 *   GENERATE_START            — generate, then start; no settled-wait.
 *   GENERATE_START_MONITOR    — generate, then start, then wait-until-terminated.
 *   START                     — read existing manifest, start; no settled-wait.
 *   START_MONITOR             — read existing manifest, start, wait-until-terminated.
 * The trailing "_MONITOR" suffix forces operation_mode = MONITOR at parse
 * time; otherwise BASIC is implied. */
enum program_mode {
    PROGRAM_MODE_GENERATE,
    PROGRAM_MODE_GENERATE_START,
    PROGRAM_MODE_GENERATE_START_MONITOR,
    PROGRAM_MODE_START,
    PROGRAM_MODE_START_MONITOR
};

/* Settled-condition mode for manager_run. Independently parsed from
 * OPERATION_MODE in Launcher.config, but PROGRAM_MODE forces it onto the
 * implied value (MONITOR for *_MONITOR program modes, BASIC otherwise) at
 * parse time and warns on conflict. Read only by manager_run. */
enum operation_mode {
    OPERATION_MODE_BASIC,     /* Exit when every instance is started (active) or aborted. */
    OPERATION_MODE_MONITOR    /* Exit when every instance is exited or aborted.           */
};

/* Lifecycle phase for one instance — the result of phase_of() called against
 * the live tracked_unit. "Intermediate" means "in between settled states"
 * (not the systemd-vocabulary 'transient', which describes units created via
 * API without an on-disk unit file — see systemd.unit(5) UnitFileState). The
 * FAILED_INTERMEDIATE phase is distinct from FAILED_ABORT because a Restart-
 * policy unit's failed/failed state is only terminal when Result ==
 * "start-limit-hit"; otherwise systemd will auto-restart and we must keep
 * waiting. See plan: state classification. */
enum unit_phase {
    UNIT_PHASE_INTERMEDIATE,        /* in-flight; not yet at a settled state    */
    UNIT_PHASE_FAILED_INTERMEDIATE, /* failed/failed but Restart will auto-restart */
    UNIT_PHASE_SUCCEEDED_RUNNING,   /* active/running                           */
    UNIT_PHASE_SUCCEEDED_EXITED,    /* active/exited + success (oneshot+remain) */
    UNIT_PHASE_SUCCEEDED_STOPPED,   /* ran clean then stopped (inactive/dead)   */
    UNIT_PHASE_FAILED_ABORT,        /* Abort policy hit failed/failed (terminal)*/
    UNIT_PHASE_FAILED_BURST         /* Restart policy hit start-limit-hit       */
};

/* Termination policy — selected per-mode. BASIC defaults to TERM_ALL_FINISHED
 * (every systemd-terminal phase counts as settled — SUCCEEDED-*, FAILED_ABORT,
 * FAILED_BURST). TERM_ALL_SUCCEEDED is the strict "all up" alternative. MONITOR
 * uses TERM_MONITOR_ALL_TERMINATED, which excludes SUCCEEDED_RUNNING from the
 * settled set (a still-running daemon hasn't terminated yet) and adds
 * SUCCEEDED_STOPPED. The middle BASIC option "all finished or abort-failed"
 * was considered and dropped: subset of TERM_ALL_FINISHED. */
enum termination_condition {
    TERM_ALL_SUCCEEDED,           /* only SUCCEEDED-* counts; any FAILED-* aborts */
    TERM_ALL_FINISHED,            /* every systemd-terminal phase settles (BASIC) */
    TERM_MONITOR_ALL_TERMINATED   /* settled = SUCCEEDED_EXITED/STOPPED + FAILED-* */
};

/* ---------------------------------------------------------------------------
 * Instance — unified runtime representation of one unit declared in the
 * manifest. There is no built-in kind distinction; inter-instance relations
 * are expressed entirely through the library-defined hierarchical mappings
 * (see NAMINGS.md and the mapping_* fields below).
 *
 * Scalar fields are fixed-size buffers. Mapping fields are pointer arrays
 * into instance_list, populated by a second resolver pass after all
 * instances are parsed from the manifest.
 * --------------------------------------------------------------------------- */

struct instance {

    char name[LIMIT_ENTRY_NAME];
    char unit_file_name[LIMIT_UNIT_NAME];
    char type[LIMIT_TYPE_NAME];
    char path[PATH_MAX];
    char command[LIMIT_FILE_BUFFER];
    /* Instance-level failure policy. Single enum, not per-mapping: a unit
     * either aborts on failure or is restarted (by systemd's emitted
     * Restart= directive). See NAMINGS.md. */
    enum failure_behavior   failure_behavior;

    /* Hierarchical mapping membership. Five parallel arrays of outer length
     * mapping_count (the file-scope global). For each mapping m:
     *   - after_lengths[m]  / after_list[m][k]   — peers this instance follows
     *   - before_lengths[m] / before_list[m][k]  — peers this instance precedes
     *   - mapping_behaviors[m]                   — Ignore / Cascade
     * See NAMINGS.md. */
    int                    *after_lengths;
    struct instance      ***after_list;
    int                    *before_lengths;
    struct instance      ***before_list;
    enum mapping_behavior  *mapping_behaviors;

    /* Pointer to the tracked_unit owned by dbus_tracker. Set after
     * tracked_units_register + attach. All runtime state (active_state,
     * sub_state, load_state, exec_main_pid, etc.) is read from this
     * struct — Manager does not duplicate it. */
    tracked_unit *tracked;

    /* Lifecycle bookkeeping maintained by on_unit_state_changed for BASIC's
     * settled-state accounting. lifecycle_phase is recomputed via phase_of()
     * on every relevant PropertiesChanged event. first_activating_usec is captured
     * the first time the instance is observed entering ACTIVATING (0 until
     * then). deadline_usec is computed alongside first_activating_usec from
     * the unit's TimeoutStartUSec * StartLimitBurst (0 = no deadline armed
     * yet, UINT64_MAX = infinity / no deadline by design). */
    enum unit_phase lifecycle_phase;
    uint64_t        first_activating_usec;
    uint64_t        deadline_usec;

    /* Latched true the first time on_unit_state_changed observes
     * ActiveState=ACTIVE. Used by phase_of() to distinguish
     * inactive/dead-after-running (SUCCEEDED_STOPPED) from
     * inactive/dead-never-started (INTERMEDIATE). */
    bool            was_active;
};

/* ---------------------------------------------------------------------------
 * Global instance list
 * --------------------------------------------------------------------------- */

static struct instance *instance_list = NULL;
static int instance_count = 0;

/* ---------------------------------------------------------------------------
 * Hierarchical mapping schema — program-globals shared by all instances.
 * See NAMINGS.md.
 * --------------------------------------------------------------------------- */

static int    mapping_count = 0;
static char **mapping_names = NULL;

/* ---------------------------------------------------------------------------
 * Configuration — all paths resolved to absolute by manager_read_config.
 * --------------------------------------------------------------------------- */

static char *config_file_path            = NULL;
static char *results_folder              = NULL;
static char *unit_file_staging_directory = NULL;
static char *unit_file_destination       = NULL;
static char *venv_directory              = NULL;
static char *input_file_path             = NULL;
static char *launcher_script_path        = NULL;
static char *manifest_file_path          = NULL;
static char *log_directory               = NULL;
static char *failure_behavior_default    = NULL;
static char *mapping_behavior_default = NULL;

/* Step-selection mode, populated by manager_read_config from PROGRAM_MODE.
 * Default GENERATE_START_MONITOR (full pipeline). Read only by main() to
 * gate which steps run. */
static enum program_mode program_mode = PROGRAM_MODE_GENERATE_START_MONITOR;

/* Settled-condition mode, populated by manager_read_config from
 * OPERATION_MODE and then overridden by PROGRAM_MODE on conflict. Default
 * OPERATION_MODE_MONITOR to match the program_mode default. Read only by
 * manager_run to pick a dispatch branch. */
static enum operation_mode operation_mode = OPERATION_MODE_MONITOR;

static inline int program_mode_has_generate(void) {
    return program_mode == PROGRAM_MODE_GENERATE
        || program_mode == PROGRAM_MODE_GENERATE_START
        || program_mode == PROGRAM_MODE_GENERATE_START_MONITOR;
}

static inline int program_mode_has_run(void) {
    /* Every mode except pure GENERATE proceeds through register/capture/run. */
    return program_mode != PROGRAM_MODE_GENERATE;
}

static const char *program_mode_to_string(enum program_mode pm) {
    switch (pm) {
        case PROGRAM_MODE_GENERATE:                return "generate";
        case PROGRAM_MODE_GENERATE_START:          return "generate-start";
        case PROGRAM_MODE_GENERATE_START_MONITOR:  return "generate-start-monitor";
        case PROGRAM_MODE_START:                   return "start";
        case PROGRAM_MODE_START_MONITOR:           return "start-monitor";
    }
    return "unknown";
}

/* Per-mode exit policies. Hardcoded — not exposed in Launcher.config (keep
 * config simple). Change here to switch policy site-wide. */
static const enum termination_condition basic_termination_condition   = TERM_ALL_FINISHED;
static const enum termination_condition monitor_termination_condition = TERM_MONITOR_ALL_TERMINATED;

/* ---------------------------------------------------------------------------
 * Step logger — chronological narrative of the basic pipeline.
 *
 * manager_log is opened by logger_init once log_directory exists (between
 * steps 3 and 4). Steps 1–3 log to stdout / stderr only. Every log_info /
 * log_err emits to stdout (or stderr) immediately and, if manager_log is
 * open, also to manager_log with a wall-clock timestamp prefix.
 * --------------------------------------------------------------------------- */

#define STEP_TOTAL 9

static FILE            *manager_log      = NULL;
static struct timespec  step_start_time  = {0};
static struct timespec  session_start_time   = {0};
static long long        step_start_offset_us = 0;
static volatile sig_atomic_t terminating = 0;

/* ---------------------------------------------------------------------------
 * Forward declarations
 * --------------------------------------------------------------------------- */

/* Manager */
int manager_init();
int manager_tear_down();
int manager_read_config           (const char *filename);
int manager_read_input            ();
int manager_read_manifest         (const char *filename);
int manager_copy_unit_files       ();
int manager_register_tracked_units();
int manager_capture_instances     ();
int manager_operate_instance      (struct instance *ins, enum instance_operation op);
int manager_terminate_instances   ();
int manager_run                   ();
int manager_run_basic             ();
int manager_run_monitor           ();
static enum unit_phase phase_of   (const struct instance *ins);
static void arm_start_deadline    (struct instance *ins, uint64_t anchor_usec, const char *why);
static int  is_settled            (enum unit_phase c, enum termination_condition cond);
static int  is_disqualifying      (enum unit_phase c, enum termination_condition cond);
static uint64_t now_monotonic_usec(void);
static const char *unit_phase_to_string(enum unit_phase c);
static struct instance *find_instance_by_tracked(const tracked_unit *u);
static void install_signal_handlers(void);
static void signal_handler        (int signo);
static void on_unit_state_changed(const tracked_unit *u,
                                  const char *property_name,
                                  void *userdata);

/* Debug / logger */
void debug_print_instances(int fd, const char *dump_file_name);
int  logger_init(const char *log_dir);
void logger_tear_down(void);
void log_info(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void log_err (const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void step_begin(int idx, const char *step_name);
void step_end  (int idx, const char *step_name, int rc);

/* Utility */
int         path_resolver              (char *abspath, const char *path);
int         build_path                 (char *out, ...);
int         create_results_folder      ();
int         create_unit_file_dump_dir  ();
int         create_log_dir             ();
int         create_runtime_dirs        ();
int         copy_file                  (const char *source, const char *destination);
int         check_file_exists          (const char *filepath);
const char *failure_behavior_to_string (enum failure_behavior fb);
const char *mapping_behavior_to_string (enum mapping_behavior mb);

/* ---------------------------------------------------------------------------
 * Step runner macros.
 *
 * RUN_STEP wraps a forward-progress step (1–8): banner in, run, banner out;
 * a negative return aborts the pipeline by jumping to the `cleanup:` label.
 * Forward steps are conditionally dispatched in main() per the active
 * program_mode — skipped steps emit no banners and no TIMING rows.
 *
 * RUN_CLEANUP_STEP wraps the cleanup step (9): still logs the banner and
 * result, but never aborts — cleanup must run to completion so the system
 * is left in a sane state. The explicit numeric index keeps the [N/9]
 * label accurate regardless of which forward step triggered the jump.
 * --------------------------------------------------------------------------- */

#define RUN_STEP(idx, name, expr)                           \
    do {                                                     \
        step_begin((idx), #name);                            \
        int _rc = (expr);                                    \
        step_end((idx), #name, _rc);                         \
        if (_rc < 0) goto cleanup;                           \
    } while (0)

#define RUN_CLEANUP_STEP(idx, name, expr)                   \
    do {                                                     \
        step_begin((idx), #name);                            \
        int _rc = (expr);                                    \
        step_end((idx), #name, _rc);                         \
    } while (0)

/* ---------------------------------------------------------------------------
 * main
 * --------------------------------------------------------------------------- */

int main() {

    int rc = 0;

    clock_gettime(CLOCK_MONOTONIC, &session_start_time);

    install_signal_handlers();

    RUN_STEP(1, manager_init,                  (rc = manager_init()));
    RUN_STEP(2, manager_read_config,           (rc = manager_read_config("./etc/Launcher.config")));
    RUN_STEP(3, create_runtime_dirs,           (rc = create_runtime_dirs()));

    /* Step 4 — input acquisition. G-modes invoke the Python pipeline to
     * generate a fresh manifest from INPUT_FILE; S-modes load an existing
     * manifest written by a prior G-mode run. */
    if (program_mode_has_generate()) {
        RUN_STEP(4, manager_read_input,        (rc = manager_read_input()));
    } else {
        RUN_STEP(4, manager_read_manifest,     (rc = manager_read_manifest(manifest_file_path)));
    }

    /* Step 5 — copy generated unit files into UNIT_FILE_DESTINATION. Only
     * applies when the spine actually generated them this run. */
    if (program_mode_has_generate()) {
        RUN_STEP(5, manager_copy_unit_files,   (rc = manager_copy_unit_files()));
    }

    /* Steps 6–8 — register tracked units with the D-Bus tracker, capture
     * their initial state, then run (which itself issues StartUnit and
     * dispatches the settled-condition wait). Skipped only for pure
     * GENERATE mode. */
    if (program_mode_has_run()) {
        RUN_STEP(6, manager_register_tracked_units,(rc = manager_register_tracked_units()));
        RUN_STEP(7, manager_capture_instances,     (rc = manager_capture_instances()));
        RUN_STEP(8, manager_run,                   (rc = manager_run()));
    }

cleanup:
    RUN_CLEANUP_STEP(9, manager_tear_down,         manager_tear_down());

    {
        struct timespec end_time;
        clock_gettime(CLOCK_MONOTONIC, &end_time);
        long long total_us = (end_time.tv_sec  - session_start_time.tv_sec)  * 1000000LL
                           + (end_time.tv_nsec - session_start_time.tv_nsec) / 1000LL;
        printf("TIMING|c|0|total|0|%lld|%lld|%d\n",
               total_us, total_us, rc < 0 ? -1 : 0);
        fflush(stdout);
    }

    return rc < 0 ? 1 : 0;
}

/* ---------------------------------------------------------------------------
 * Manager functions
 * --------------------------------------------------------------------------- */

int manager_init() {

    config_file_path            = (char *) calloc(PATH_MAX, sizeof(char));
    results_folder              = (char *) calloc(PATH_MAX, sizeof(char));
    unit_file_staging_directory = (char *) calloc(PATH_MAX, sizeof(char));
    unit_file_destination       = (char *) calloc(PATH_MAX, sizeof(char));
    venv_directory              = (char *) calloc(PATH_MAX, sizeof(char));
    input_file_path             = (char *) calloc(PATH_MAX, sizeof(char));
    launcher_script_path        = (char *) calloc(PATH_MAX, sizeof(char));
    manifest_file_path          = (char *) calloc(PATH_MAX, sizeof(char));
    log_directory               = (char *) calloc(PATH_MAX, sizeof(char));
    failure_behavior_default    = (char *) calloc(LIMIT_FAILURE_BEHAVIOR, sizeof(char));
    mapping_behavior_default = (char *) calloc(LIMIT_FAILURE_BEHAVIOR, sizeof(char));

    int rc;

    log_info("[init] connecting to user bus");
    rc = dbus_init();
    if (rc < 0) { log_err("[init] dbus_init rc=%d", rc); return rc; }

    /* Match listeners are installed BEFORE Subscribe. sd_bus_call_method's
     * internal sd_bus_process loop (used while waiting for the Subscribe
     * reply) dispatches any signals systemd emits in that window; unmatched
     * signals are silently dropped. With matches armed first, dispatched
     * signals find filters and are caught. */
    log_info("[init] arming Reloading listener");
    rc = dbus_daemon_reload_listener();
    if (rc < 0) { log_err("[init] Reloading listener rc=%d", rc); return rc; }

    log_info("[init] arming UnitNew listener");
    rc = dbus_unit_new_listener();
    if (rc < 0) { log_err("[init] UnitNew listener rc=%d", rc); return rc; }

    log_info("[init] arming UnitRemoved listener");
    rc = dbus_unit_removed_listener();
    if (rc < 0) { log_err("[init] UnitRemoved listener rc=%d", rc); return rc; }

    log_info("[init] subscribing to systemd");
    rc = dbus_subscribe_systemd();
    if (rc < 0) { log_err("[init] subscribe rc=%d", rc); return rc; }

    /* Active verification. */
    if (bus == NULL) {
        log_err("[init] verify: bus is NULL");
        return -1;
    }
    const char *unique = NULL;
    rc = sd_bus_get_unique_name(bus, &unique);
    if (rc < 0) {
        log_err("[init] verify: sd_bus_get_unique_name rc=%d", rc);
        return rc;
    }
    if (!slot || !slot_reloading || !slot_unit_new || !slot_unit_removed) {
        log_err("[init] verify: a listener slot is NULL "
                "(slot=%p reloading=%p unit_new=%p unit_removed=%p)",
                (void *) slot,
                (void *) slot_reloading,
                (void *) slot_unit_new,
                (void *) slot_unit_removed);
        return -1;
    }
    log_info("[init] verify: bus unique=%s, 3/3 listeners armed",
             unique ? unique : "(null)");

    return 0;
}

int manager_tear_down() {

    int rc;

    log_info("[tear_down] unsubscribing from systemd");
    rc = dbus_unsubscribe_systemd();
    if (rc < 0) log_err("[tear_down] unsubscribe rc=%d", rc);

    log_info("[tear_down] closing sd-bus");
    dbus_tear_down();

    free(config_file_path);
    free(results_folder);
    free(unit_file_staging_directory);
    free(unit_file_destination);
    free(venv_directory);
    free(input_file_path);
    free(launcher_script_path);
    free(manifest_file_path);
    free(log_directory);
    free(failure_behavior_default);
    free(mapping_behavior_default);

    /* Free per-instance hierarchical-mapping arrays. */
    if (instance_list != NULL) {
        int i, m;
        for (i = 0; i < instance_count; i++) {
            if (instance_list[i].after_list != NULL) {
                for (m = 0; m < mapping_count; m++) {
                    free(instance_list[i].after_list[m]);
                }
                free(instance_list[i].after_list);
            }
            if (instance_list[i].before_list != NULL) {
                for (m = 0; m < mapping_count; m++) {
                    free(instance_list[i].before_list[m]);
                }
                free(instance_list[i].before_list);
            }
            free(instance_list[i].after_lengths);
            free(instance_list[i].before_lengths);
            free(instance_list[i].mapping_behaviors);
        }
        free(instance_list);
    }

    /* Free global mapping schema. */
    if (mapping_names != NULL) {
        int m;
        for (m = 0; m < mapping_count; m++) {
            free(mapping_names[m]);
        }
        free(mapping_names);
        mapping_names = NULL;
    }
    mapping_count = 0;

    log_info("[tear_down] freed globals and instance list");

    logger_tear_down();

    return rc;
}

/* ---------------------------------------------------------------------------
 * Signal handling.
 *
 * Catches SIGINT, SIGTERM, SIGSEGV and routes them through a single handler
 * that invokes manager_tear_down() — the termination procedure used at the
 * normal end of main(). SIGSEGV is re-raised with SIG_DFL after cleanup so
 * the kernel still produces a core dump; SIGINT/SIGTERM exit with the
 * conventional code 128+signo.
 *
 * The `terminating` flag short-circuits recursive entry: if a second signal
 * fires while cleanup is running, we restore the default disposition and
 * re-raise immediately rather than re-entering tear_down.
 *
 * NOTE: log_*() and manager_tear_down() are NOT async-signal-safe in the
 * strict POSIX sense. The risk is bounded — the process is exiting anyway
 * and at worst we get garbled stderr output. The benefit (clean sd-bus
 * unsubscribe + free + log close) outweighs the cost.
 * --------------------------------------------------------------------------- */

static const char *signo_name(int signo) {
    switch (signo) {
        case SIGINT:  return "SIGINT";
        case SIGTERM: return "SIGTERM";
        case SIGSEGV: return "SIGSEGV";
        default:      return "?";
    }
}

static void signal_handler(int signo) {

    if (terminating) {
        /* Already running cleanup — fall through to default disposition. */
        signal(signo, SIG_DFL);
        raise(signo);
        return;
    }
    terminating = 1;

    log_err("[signal] caught signo=%d (%s) -> running termination procedure",
            signo, signo_name(signo));

    /* Termination step: stop every started unit, then tear down. Safe when
     * instance_count == 0 (signal before manifest parsed) — the inner loop
     * runs zero iterations. */
    manager_terminate_instances();

    manager_tear_down();

    if (signo == SIGSEGV) {
        /* Restore default so the kernel produces a core dump. */
        signal(signo, SIG_DFL);
        raise(signo);
        return;
    }

    /* Conventional exit code for signal termination: 128 + signo. */
    _exit(128 + signo);
}

static void install_signal_handlers(void) {

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;

    sigaction(SIGINT,  &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGSEGV, &sa, NULL);

    log_info("[signal] handlers installed (SIGINT, SIGTERM, SIGSEGV)");
}

/* ---------------------------------------------------------------------------
 * Configuration functions
 *
 * Strict line-oriented KEY=value file. Keys are UPPERCASE with `_`
 * separators. Lines starting with `#` and blank lines are skipped.
 *
 * Recognized keys:
 *   RESULTS_FOLDER, UNIT_FILE_STAGING_DIRECTORY, UNIT_FILE_DESTINATION,
 *   VENV_DIRECTORY, INPUT_FILE, LAUNCHER_SCRIPT, MANIFEST_FILE,
 *   LOG_DIRECTORY, PROGRAM_MODE, OPERATION_MODE,
 *   FAILURE_BEHAVIOR_DEFAULT, MAPPING_BEHAVIOR_DEFAULT.
 *
 * All eight path values are routed through path_resolver (~ expansion +
 * realpath). PROGRAM_MODE accepts one of {generate, generate-start,
 * generate-start-monitor, start, start-monitor} and selects which spine
 * steps run. OPERATION_MODE accepts "monitor" or "basic" and is the
 * settled-condition for manager_run; PROGRAM_MODE forces it onto the
 * implied value (MONITOR for *_MONITOR program modes, BASIC otherwise)
 * after parsing and warns on conflict. FAILURE_BEHAVIOR_DEFAULT and
 * MAPPING_BEHAVIOR_DEFAULT default to "Ignore" when omitted.
 *
 * Returns 0 on success, -1 if a required path is empty or one of the
 * pre-existing inputs (VENV_DIRECTORY, INPUT_FILE, LAUNCHER_SCRIPT) is
 * inaccessible.
 * --------------------------------------------------------------------------- */

int manager_read_config(const char *filename) {

    path_resolver(config_file_path, filename);
    log_info("[read_config] opening %s", config_file_path);

    char *contents = (char *) calloc(LIMIT_FILE_BUFFER, sizeof(char));

    FILE *f = fopen(config_file_path, "r");
    if (f == NULL) {
        log_err("[read_config] cannot open config file: %s (%s)",
                config_file_path, strerror(errno));
        free(contents);
        return -1;
    }

    int cursor = 0;
    while (1) {
        int n = (int) fread(&contents[cursor], sizeof(char), READ_WRITE_CHUNK, f);
        if (n <= 0) break;
        cursor += n;
    }
    fclose(f);

    contents[LIMIT_FILE_BUFFER - 1] = '\0';

    char **path_to_fill                   = NULL;
    int    is_program_mode                = 0;
    int    is_operation_mode              = 0;
    int    is_failure_behavior_default    = 0;
    int    is_mapping_behavior_default = 0;
    int    start = 0, end = 0, mode = 0;

    /* Track whether each mode key appeared explicitly in the config so we
     * can warn precisely on PROGRAM_MODE ↔ OPERATION_MODE conflicts after
     * the parse loop completes. */
    int    program_mode_explicit          = 0;
    int    operation_mode_explicit        = 0;

    while (1) {

        /* Skip blank lines and comment lines. */
        if (mode == 0 && (contents[end] == '\n' || contents[end] == '#')) {
            while (contents[end] != '\n' && contents[end] != '\0') end++;
            if (contents[end] == '\0') break;
            end++;
            start = end;
            continue;
        }

        if (contents[end] == '=' && mode == 0) {

            unsigned long dist = end - start;

            path_to_fill                   = NULL;
            is_program_mode                = 0;
            is_operation_mode              = 0;
            is_failure_behavior_default    = 0;
            is_mapping_behavior_default = 0;

            if      (!strncmp("RESULTS_FOLDER",              &contents[start], dist)) path_to_fill                   = &results_folder;
            else if (!strncmp("UNIT_FILE_STAGING_DIRECTORY", &contents[start], dist)) path_to_fill                   = &unit_file_staging_directory;
            else if (!strncmp("UNIT_FILE_DESTINATION",       &contents[start], dist)) path_to_fill                   = &unit_file_destination;
            else if (!strncmp("VENV_DIRECTORY",              &contents[start], dist)) path_to_fill                   = &venv_directory;
            else if (!strncmp("INPUT_FILE",                  &contents[start], dist)) path_to_fill                   = &input_file_path;
            else if (!strncmp("LAUNCHER_SCRIPT",             &contents[start], dist)) path_to_fill                   = &launcher_script_path;
            else if (!strncmp("MANIFEST_FILE",               &contents[start], dist)) path_to_fill                   = &manifest_file_path;
            else if (!strncmp("LOG_DIRECTORY",               &contents[start], dist)) path_to_fill                   = &log_directory;
            else if (!strncmp("PROGRAM_MODE",                &contents[start], dist)) is_program_mode                = 1;
            else if (!strncmp("OPERATION_MODE",              &contents[start], dist)) is_operation_mode              = 1;
            else if (!strncmp("FAILURE_BEHAVIOR_DEFAULT",    &contents[start], dist)) is_failure_behavior_default    = 1;
            else if (!strncmp("MAPPING_BEHAVIOR_DEFAULT",    &contents[start], dist)) is_mapping_behavior_default    = 1;

            mode = 1;
            end++;
            start = end;

        } else if (contents[end] == '\n' && mode == 1) {

            unsigned long dist = end - start;
            char raw[PATH_MAX] = {0};
            strncpy(raw, &contents[start], dist);

            if (path_to_fill != NULL) {
                path_resolver(*path_to_fill, raw);
            } else if (is_program_mode) {
                program_mode_explicit = 1;
                if      (!strncmp("generate-start-monitor", raw, dist) && dist == 22) program_mode = PROGRAM_MODE_GENERATE_START_MONITOR;
                else if (!strncmp("generate-start",         raw, dist) && dist == 14) program_mode = PROGRAM_MODE_GENERATE_START;
                else if (!strncmp("generate",               raw, dist) && dist ==  8) program_mode = PROGRAM_MODE_GENERATE;
                else if (!strncmp("start-monitor",          raw, dist) && dist == 13) program_mode = PROGRAM_MODE_START_MONITOR;
                else if (!strncmp("start",                  raw, dist) && dist ==  5) program_mode = PROGRAM_MODE_START;
                else {
                    log_err("[read_config] PROGRAM_MODE unrecognized value '%s' — falling back to generate-start-monitor",
                            raw);
                    program_mode = PROGRAM_MODE_GENERATE_START_MONITOR;
                }
            } else if (is_operation_mode) {
                operation_mode_explicit = 1;
                if      (!strncmp("monitor", raw, dist) && dist == 7) operation_mode = OPERATION_MODE_MONITOR;
                else if (!strncmp("basic",   raw, dist) && dist == 5) operation_mode = OPERATION_MODE_BASIC;
                else {
                    log_err("[read_config] OPERATION_MODE unrecognized value '%s' — falling back to basic",
                            raw);
                    operation_mode = OPERATION_MODE_BASIC;
                }
            } else if (is_failure_behavior_default) {
                strncpy(failure_behavior_default, raw, LIMIT_FAILURE_BEHAVIOR - 1);
                failure_behavior_default[LIMIT_FAILURE_BEHAVIOR - 1] = '\0';
            } else if (is_mapping_behavior_default) {
                strncpy(mapping_behavior_default, raw, LIMIT_FAILURE_BEHAVIOR - 1);
                mapping_behavior_default[LIMIT_FAILURE_BEHAVIOR - 1] = '\0';
            }

            path_to_fill                   = NULL;
            is_program_mode                = 0;
            is_operation_mode              = 0;
            is_failure_behavior_default    = 0;
            is_mapping_behavior_default = 0;
            mode  = 0;
            end++;
            start = end;

            if (contents[end] == '\0') break;

        } else {
            end++;
        }

        if (contents[end] == '\0') break;
    }

    free(contents);

    /* Fall back to the hardcoded default when Launcher.config omits it. */
    if (!failure_behavior_default[0]) {
        strncpy(failure_behavior_default, "Abort", LIMIT_FAILURE_BEHAVIOR - 1);
    }
    if (!mapping_behavior_default[0]) {
        strncpy(mapping_behavior_default, "Ignore", LIMIT_FAILURE_BEHAVIOR - 1);
    }

    /* PROGRAM_MODE overrides OPERATION_MODE on conflict. The *_MONITOR
     * program modes imply OPERATION_MODE_MONITOR; the rest imply BASIC.
     * Pure GENERATE never reaches manager_run so the implied value is
     * inert but we still normalise it for consistency. */
    enum operation_mode implied_operation_mode;
    switch (program_mode) {
        case PROGRAM_MODE_GENERATE_START_MONITOR:
        case PROGRAM_MODE_START_MONITOR:
            implied_operation_mode = OPERATION_MODE_MONITOR;
            break;
        case PROGRAM_MODE_GENERATE_START:
        case PROGRAM_MODE_START:
        case PROGRAM_MODE_GENERATE:
        default:
            implied_operation_mode = OPERATION_MODE_BASIC;
            break;
    }

    if (operation_mode_explicit && operation_mode != implied_operation_mode) {
        log_err("[read_config] OPERATION_MODE=%s conflicts with PROGRAM_MODE=%s — overriding to %s",
                operation_mode == OPERATION_MODE_MONITOR ? "monitor" : "basic",
                program_mode_to_string(program_mode),
                implied_operation_mode == OPERATION_MODE_MONITOR ? "monitor" : "basic");
    }
    operation_mode = implied_operation_mode;

    (void) program_mode_explicit;  /* reserved for future "warn if omitted" */

    /* Per-action echo of every parsed value. */
    log_info("[read_config] RESULTS_FOLDER              = %s", results_folder);
    log_info("[read_config] UNIT_FILE_STAGING_DIRECTORY = %s", unit_file_staging_directory);
    log_info("[read_config] UNIT_FILE_DESTINATION       = %s", unit_file_destination);
    log_info("[read_config] VENV_DIRECTORY              = %s", venv_directory);
    log_info("[read_config] INPUT_FILE                  = %s", input_file_path);
    log_info("[read_config] LAUNCHER_SCRIPT             = %s", launcher_script_path);
    log_info("[read_config] MANIFEST_FILE               = %s", manifest_file_path);
    log_info("[read_config] LOG_DIRECTORY               = %s", log_directory);
    log_info("[read_config] PROGRAM_MODE                = %s", program_mode_to_string(program_mode));
    log_info("[read_config] OPERATION_MODE              = %s",
             operation_mode == OPERATION_MODE_MONITOR ? "monitor" : "basic");
    log_info("[read_config] FAILURE_BEHAVIOR_DEFAULT    = %s", failure_behavior_default);
    log_info("[read_config] MAPPING_BEHAVIOR_DEFAULT    = %s", mapping_behavior_default);

    /* Active verification. */
    int missing = 0;
    if (!results_folder[0])              { log_err("[read_config] verify: RESULTS_FOLDER empty");              missing++; }
    if (!unit_file_staging_directory[0]) { log_err("[read_config] verify: UNIT_FILE_STAGING_DIRECTORY empty"); missing++; }
    if (!unit_file_destination[0])       { log_err("[read_config] verify: UNIT_FILE_DESTINATION empty");       missing++; }
    if (!venv_directory[0])              { log_err("[read_config] verify: VENV_DIRECTORY empty");              missing++; }
    if (!input_file_path[0])             { log_err("[read_config] verify: INPUT_FILE empty");                  missing++; }
    if (!launcher_script_path[0])        { log_err("[read_config] verify: LAUNCHER_SCRIPT empty");             missing++; }
    if (!manifest_file_path[0])          { log_err("[read_config] verify: MANIFEST_FILE empty");               missing++; }
    if (!log_directory[0])               { log_err("[read_config] verify: LOG_DIRECTORY empty");               missing++; }
    if (missing) return -1;

    /* Pre-existing inputs: venv, input file, launcher script must be
     * accessible before any downstream step touches them. */
    if (access(venv_directory,       F_OK) != 0) { log_err("[read_config] verify: VENV_DIRECTORY not accessible: %s (%s)",  venv_directory,       strerror(errno)); return -1; }
    if (access(input_file_path,      F_OK) != 0) { log_err("[read_config] verify: INPUT_FILE not accessible: %s (%s)",      input_file_path,      strerror(errno)); return -1; }
    if (access(launcher_script_path, F_OK) != 0) { log_err("[read_config] verify: LAUNCHER_SCRIPT not accessible: %s (%s)", launcher_script_path, strerror(errno)); return -1; }

    log_info("[read_config] verify: 8/8 required paths non-empty; pre-existing inputs accessible");

    return 0;
}
/* ---------------------------------------------------------------------------
 * Input stage — forks the Python launcher (UnitGenerator.py) inside the
 * configured venv, waits for it to emit the manifest, parses the manifest
 * via manager_read_manifest, then actively verifies that every instance has
 * non-empty identity fields and every per-mapping peer reference resolved
 * to a live instance pointer.
 *
 * Adapted from __old_source for the generalized mapping schema: identity
 * verification drops the removed `key` field; per-relation verification
 * loops over after_lengths[m] / after_list[m][k] and the matching before_*
 * pair instead of the old fixed after/before/group/members arrays.
 * --------------------------------------------------------------------------- */

int manager_read_input() {

    log_info("[read_input] forking launcher");
    pid_t child = fork();

    if (child > 0) {

        int status;
        pid_t ret;
        do { ret = waitpid(child, &status, 0); } while (ret < 0 && errno == EINTR);
        if (ret < 0) {
            log_err("[read_input] waitpid failed: %s", strerror(errno));
            return -1;
        }
        if (!WIFEXITED(status)) {
            log_err("[read_input] launcher did not exit normally (status=0x%x)", status);
            return -1;
        }
        int exit_code = WEXITSTATUS(status);
        log_info("[read_input] launcher exited %d", exit_code);
        if (exit_code != 0) {
            log_err("[read_input] launcher returned non-zero (%d)", exit_code);
            return -1;
        }

        if (access(manifest_file_path, F_OK) != 0) {
            log_err("[read_input] manifest not found: %s (%s)",
                    manifest_file_path, strerror(errno));
            return -1;
        }

        log_info("[read_input] parsing manifest %s", manifest_file_path);
        int rc = manager_read_manifest(manifest_file_path);
        if (rc < 0) {
            log_err("[read_input] manifest parse failed rc=%d", rc);
            return rc;
        }

        log_info("[read_input] %d instances parsed", instance_count);

        /* Active verification. */
        if (instance_count <= 0) {
            log_err("[read_input] verify: no instances parsed");
            return -1;
        }

        int unresolved = 0;
        for (int i = 0; i < instance_count; i++) {
            struct instance *ins = &instance_list[i];

            if (!ins->name[0] || !ins->unit_file_name[0]) {
                log_err("[read_input] verify: instance %d has empty identity "
                        "field (name='%s' unit='%s')",
                        i, ins->name, ins->unit_file_name);
                return -1;
            }
            for (int m = 0; m < mapping_count; m++) {
                const char *mname = mapping_names[m] ? mapping_names[m] : "(unnamed)";
                int alen = ins->after_lengths ? ins->after_lengths[m] : 0;
                for (int k = 0; k < alen; k++) {
                    if (ins->after_list[m][k] == NULL) {
                        unresolved++;
                        log_err("[read_input] verify: %s after[%s][%d] unresolved",
                                ins->name, mname, k);
                    }
                }
                int blen = ins->before_lengths ? ins->before_lengths[m] : 0;
                for (int k = 0; k < blen; k++) {
                    if (ins->before_list[m][k] == NULL) {
                        unresolved++;
                        log_err("[read_input] verify: %s before[%s][%d] unresolved",
                                ins->name, mname, k);
                    }
                }
            }
        }
        if (unresolved > 0) {
            log_err("[read_input] verify: %d unresolved relationship keys", unresolved);
            return -1;
        }
        log_info("[read_input] verify: %d instances, all relationship keys resolved",
                 instance_count);

        return 0;

    } else if (child == 0) {

        char *launcher_cmd       = (char *) calloc(1 << 16, sizeof(char));
        char  activate[PATH_MAX] = {0};
        build_path(activate, venv_directory, "bin/activate", (char *) NULL);
        int cmd_len = snprintf(launcher_cmd, 1 << 16,
                 "source %s && PYTHONPATH=. python3 %s "
                 "INPUT_FILE=%s "
                 "UNIT_FILE_OUTPUT_PATH=%s "
                 "MANIFEST_FILE_PATH=%s "
                 "FAILURE_BEHAVIOR_DEFAULT=%s "
                 "MAPPING_BEHAVIOR_DEFAULT=%s "
                 "&& deactivate",
                 activate,
                 launcher_script_path,
                 input_file_path,
                 unit_file_staging_directory,
                 manifest_file_path,
                 failure_behavior_default,
                 mapping_behavior_default
        );
        if (cmd_len < 0 || cmd_len >= (1 << 16)) {
            fprintf(stderr,
                    "[read_input] launcher command rendering failed (cmd_len=%d, cap=%d)\n",
                    cmd_len, 1 << 16);
            free(launcher_cmd);
            _exit(127);
        }
        execl("/bin/bash", "bash", "-c", launcher_cmd, (char *) NULL);
        /* Only reached on execl failure. */
        free(launcher_cmd);
        _exit(127);

    } else {
        log_err("[read_input] fork failed: %s", strerror(errno));
        return -1;
    }
}
/* ---------------------------------------------------------------------------
 * Manifest functions
 *
 * The manifest is a strict line-oriented format consisting of an optional
 * leading GLOBALS block followed by zero or more INSTANCE blocks. Each
 * top-level block is delimited by an `END` marker on its own line.
 *
 *   GLOBALS
 *   hierarchical_mappings=<space-separated mapping names>
 *   END
 *
 *   INSTANCE
 *   name=<unique identifier>
 *   type=<library-defined string>
 *   unit_file_name=<…>
 *   path=<optional>
 *   command=<optional>
 *   failure_behavior=<Abort|Restart>
 *
 *   MAPPING <mapping-name>
 *   after=<space-separated peer names>
 *   before=<space-separated peer names>
 *   mapping_behavior=<Ignore|Cascade>
 *   END_MAPPING
 *   …                                              (one MAPPING sub-block per
 *                                                   declared mapping the
 *                                                   instance is enrolled in;
 *                                                   omit entirely if not
 *                                                   enrolled)
 *   END
 *
 * Cross-references inside `after=` and `before=` lines are resolved against
 * `name` (no `key` field). Both lines are required per MAPPING sub-block;
 * either may have an empty value if this instance has no peers on that
 * side of the relation. The reader rejects unknown line keys, duplicate
 * names, unresolved peer references, duplicate MAPPING sub-blocks for the
 * same mapping in one instance, MAPPING sub-blocks that miss any of the
 * four required lines, behavior tokens outside the enum value sets, and
 * unclosed blocks.
 * --------------------------------------------------------------------------- */

static int manifest_count_list_tokens(const char *str);
static int manifest_find_mapping_index(const char *name);
static int find_by_name              (const char *name, struct instance **out);
static int manifest_resolve_pointers (char ***scratch_after_keys,
                                      char ***scratch_before_keys);

int manager_read_manifest(const char *filename) {

    char *contents = (char *) calloc(LIMIT_FILE_BUFFER, sizeof(char));
    if (contents == NULL) {
        log_err("[read_manifest] out of memory for file buffer");
        return -1;
    }

    FILE *f = fopen(filename, "r");
    if (f == NULL) {
        log_err("[read_manifest] cannot open manifest: %s (%s)",
                filename, strerror(errno));
        free(contents);
        return -1;
    }

    int cursor = 0;
    while (1) {
        int n = (int) fread(&contents[cursor], sizeof(char),
                            READ_WRITE_CHUNK, f);
        if (n <= 0) break;
        cursor += n;
        if (cursor >= LIMIT_FILE_BUFFER - 1) break;
    }
    fclose(f);
    contents[LIMIT_FILE_BUFFER - 1] = '\0';

    /* Count INSTANCE blocks to size instance_list. */
    int count = 0;
    {
        char *scan = contents;
        while ((scan = strstr(scan, "INSTANCE\n")) != NULL) {
            count++;
            scan++;
        }
    }

    instance_count = count;
    instance_list  = (struct instance *) calloc((size_t) instance_count,
                                                sizeof(struct instance));

    /* Parser-local scratch holding raw key strings per (instance, mapping)
     * until the resolution pass. Two parallel tables — one for `after=`
     * peers, one for `before=` peers. Discarded before return. */
    char ***scratch_after_keys  = NULL;
    char ***scratch_before_keys = NULL;

    int rc = 0;
    int idx = -1;
    enum { OUTSIDE, IN_GLOBALS, IN_INSTANCE, IN_MAPPING } state = OUTSIDE;

    /* Per-MAPPING-sub-block state. cur_mapping_idx is the mapping_names
     * index targeted by the current MAPPING ... END_MAPPING sub-block; the
     * three seen_* flags track which required lines have been parsed
     * inside it (reset on each MAPPING opener). */
    int cur_mapping_idx       = -1;
    int seen_after            = 0;
    int seen_before           = 0;
    int seen_mapping_behavior = 0;

    /* Per-INSTANCE state. Reset on each INSTANCE opener, validated on END. */
    int seen_failure_behavior = 0;

    int start = 0, end = 0;
    while (contents[end] != '\0') {

        if (contents[end] != '\n') { end++; continue; }

        int len = end - start;
        if (len <= 0) { end++; start = end; continue; }

        char line[LIMIT_FILE_BUFFER] = {0};
        strncpy(line, &contents[start], (size_t) len);

        if (!strcmp(line, "GLOBALS")) {
            if (state != OUTSIDE) {
                log_err("[read_manifest] unexpected GLOBALS while inside another block");
                rc = -1; goto cleanup;
            }
            if (idx >= 0) {
                log_err("[read_manifest] GLOBALS must precede all INSTANCE blocks");
                rc = -1; goto cleanup;
            }
            state = IN_GLOBALS;
        }
        else if (!strcmp(line, "INSTANCE")) {
            if (state != OUTSIDE) {
                log_err("[read_manifest] unexpected INSTANCE while inside another block");
                rc = -1; goto cleanup;
            }
            state = IN_INSTANCE;
            idx++;
            seen_failure_behavior = 0;

            /* Allocate scratch on the first INSTANCE (mapping_count is final
             * by now: GLOBALS must precede). */
            if (scratch_after_keys == NULL) {
                scratch_after_keys  = (char ***) calloc((size_t) instance_count, sizeof(char **));
                scratch_before_keys = (char ***) calloc((size_t) instance_count, sizeof(char **));
                if (mapping_count > 0) {
                    for (int i = 0; i < instance_count; i++) {
                        scratch_after_keys[i]  = (char **) calloc((size_t) mapping_count, sizeof(char *));
                        scratch_before_keys[i] = (char **) calloc((size_t) mapping_count, sizeof(char *));
                    }
                }
            }

            if (mapping_count > 0) {
                instance_list[idx].after_lengths      = (int *)                    calloc((size_t) mapping_count, sizeof(int));
                instance_list[idx].after_list         = (struct instance ***)      calloc((size_t) mapping_count, sizeof(struct instance **));
                instance_list[idx].before_lengths     = (int *)                    calloc((size_t) mapping_count, sizeof(int));
                instance_list[idx].before_list        = (struct instance ***)      calloc((size_t) mapping_count, sizeof(struct instance **));
                instance_list[idx].mapping_behaviors  = (enum mapping_behavior *)  calloc((size_t) mapping_count, sizeof(enum mapping_behavior));
            }
        }
        else if (!strcmp(line, "END")) {
            if (state == IN_MAPPING) {
                log_err("[read_manifest] INSTANCE '%s' END encountered while inside MAPPING '%s' (missing END_MAPPING)",
                        instance_list[idx].name[0] ? instance_list[idx].name : "(unnamed)",
                        (cur_mapping_idx >= 0 && mapping_names && mapping_names[cur_mapping_idx])
                            ? mapping_names[cur_mapping_idx] : "(unknown)");
                rc = -1; goto cleanup;
            }
            if (state == IN_INSTANCE) {
                struct instance *ins = &instance_list[idx];
                if (!ins->name[0])           { log_err("[read_manifest] INSTANCE #%d missing required: name",                      idx);       rc = -1; goto cleanup; }
                if (!ins->type[0])           { log_err("[read_manifest] INSTANCE '%s' missing required: type",                     ins->name); rc = -1; goto cleanup; }
                if (!ins->unit_file_name[0]) { log_err("[read_manifest] INSTANCE '%s' missing required: unit_file_name",           ins->name); rc = -1; goto cleanup; }
                if (!seen_failure_behavior)  { log_err("[read_manifest] INSTANCE '%s' missing required: failure_behavior",         ins->name); rc = -1; goto cleanup; }
                for (int k = 0; k < idx; k++) {
                    if (!strcmp(instance_list[k].name, ins->name)) {
                        log_err("[read_manifest] duplicate name '%s' at INSTANCE #%d", ins->name, idx);
                        rc = -1; goto cleanup;
                    }
                }
            }
            state = OUTSIDE;
        }
        else if (!strncmp(line, "MAPPING", 7) && (line[7] == '\0' || line[7] == ' ')) {
            if (state != IN_INSTANCE) {
                log_err("[read_manifest] unexpected MAPPING outside an INSTANCE block");
                rc = -1; goto cleanup;
            }
            const char *mname = line + 7;
            while (*mname == ' ') mname++;
            if (mname[0] == '\0') {
                log_err("[read_manifest] MAPPING line missing mapping name (on INSTANCE '%s')",
                        instance_list[idx].name[0] ? instance_list[idx].name : "(unnamed)");
                rc = -1; goto cleanup;
            }
            int m = manifest_find_mapping_index(mname);
            if (m < 0) {
                log_err("[read_manifest] MAPPING '%s' on '%s': name not declared in hierarchical_mappings",
                        mname, instance_list[idx].name[0] ? instance_list[idx].name : "(unnamed)");
                rc = -1; goto cleanup;
            }
            if (scratch_after_keys != NULL && scratch_after_keys[idx] != NULL &&
                scratch_after_keys[idx][m] != NULL) {
                log_err("[read_manifest] duplicate MAPPING '%s' sub-block in INSTANCE '%s'",
                        mname, instance_list[idx].name[0] ? instance_list[idx].name : "(unnamed)");
                rc = -1; goto cleanup;
            }
            /* Claim both mapping slots with empty sentinels; the `after=` and
             * `before=` lines will overwrite them if they have non-empty peers. */
            if (scratch_after_keys != NULL && scratch_after_keys[idx] != NULL) {
                scratch_after_keys[idx][m]  = strdup("");
            }
            if (scratch_before_keys != NULL && scratch_before_keys[idx] != NULL) {
                scratch_before_keys[idx][m] = strdup("");
            }
            cur_mapping_idx       = m;
            seen_after            = 0;
            seen_before           = 0;
            seen_mapping_behavior = 0;
            state                 = IN_MAPPING;
        }
        else if (!strcmp(line, "END_MAPPING")) {
            if (state != IN_MAPPING) {
                log_err("[read_manifest] unexpected END_MAPPING outside a MAPPING sub-block");
                rc = -1; goto cleanup;
            }
            const char *mname = (cur_mapping_idx >= 0 && mapping_names && mapping_names[cur_mapping_idx])
                                ? mapping_names[cur_mapping_idx] : "(unknown)";
            const char *iname = instance_list[idx].name[0] ? instance_list[idx].name : "(unnamed)";
            if (!seen_after) {
                log_err("[read_manifest] MAPPING '%s' on '%s' missing required: after",
                        mname, iname);
                rc = -1; goto cleanup;
            }
            if (!seen_before) {
                log_err("[read_manifest] MAPPING '%s' on '%s' missing required: before",
                        mname, iname);
                rc = -1; goto cleanup;
            }
            if (!seen_mapping_behavior) {
                log_err("[read_manifest] MAPPING '%s' on '%s' missing required: mapping_behavior",
                        mname, iname);
                rc = -1; goto cleanup;
            }
            cur_mapping_idx = -1;
            state           = IN_INSTANCE;
        }
        else if (state == IN_GLOBALS) {
            char *eq = strchr(line, '=');
            if (eq == NULL) {
                log_err("[read_manifest] malformed GLOBALS line: %s", line);
                rc = -1; goto cleanup;
            }
            *eq = '\0';
            const char *key_str   = line;
            const char *value_str = eq + 1;

            if (!strcmp(key_str, "hierarchical_mappings")) {
                int n = manifest_count_list_tokens(value_str);
                if (n > 0) {
                    mapping_count = n;
                    mapping_names = (char **) calloc((size_t) n, sizeof(char *));
                    char *tmp = strdup(value_str);
                    char *tok = strtok(tmp, " ");
                    int j = 0;
                    while (tok != NULL && j < n) {
                        mapping_names[j++] = strdup(tok);
                        tok = strtok(NULL, " ");
                    }
                    free(tmp);
                }
            } else {
                log_err("[read_manifest] unknown GLOBALS field: %s", key_str);
                rc = -1; goto cleanup;
            }
        }
        else if (state == IN_INSTANCE) {
            char *eq = strchr(line, '=');
            if (eq == NULL) {
                log_err("[read_manifest] malformed INSTANCE line: %s", line);
                rc = -1; goto cleanup;
            }
            *eq = '\0';
            const char *key_str   = line;
            char       *value_str = eq + 1;

            struct instance *ins = &instance_list[idx];

            if      (!strcmp(key_str, "name"))            strncpy(ins->name,           value_str, LIMIT_ENTRY_NAME  - 1);
            else if (!strcmp(key_str, "type"))            strncpy(ins->type,           value_str, LIMIT_TYPE_NAME   - 1);
            else if (!strcmp(key_str, "unit_file_name"))  strncpy(ins->unit_file_name, value_str, LIMIT_UNIT_NAME   - 1);
            else if (!strcmp(key_str, "path"))            strncpy(ins->path,           value_str, PATH_MAX          - 1);
            else if (!strcmp(key_str, "command"))         strncpy(ins->command,        value_str, LIMIT_FILE_BUFFER - 1);
            else if (!strcmp(key_str, "failure_behavior")) {
                if (seen_failure_behavior) {
                    log_err("[read_manifest] duplicate 'failure_behavior=' on INSTANCE '%s'",
                            ins->name[0] ? ins->name : "(unnamed)");
                    rc = -1; goto cleanup;
                }
                if      (!strcmp(value_str, "Abort"))   ins->failure_behavior = FAILURE_BEHAVIOR_ABORT;
                else if (!strcmp(value_str, "Restart")) ins->failure_behavior = FAILURE_BEHAVIOR_RESTART;
                else {
                    log_err("[read_manifest] INSTANCE '%s': invalid failure_behavior '%s' (expected 'Abort' or 'Restart')",
                            ins->name[0] ? ins->name : "(unnamed)", value_str);
                    rc = -1; goto cleanup;
                }
                seen_failure_behavior = 1;
            }
            else {
                log_err("[read_manifest] unknown INSTANCE field: %s (on '%s')",
                        key_str, ins->name[0] ? ins->name : "(unnamed)");
                rc = -1; goto cleanup;
            }
        }
        else if (state == IN_MAPPING) {
            char *eq = strchr(line, '=');
            if (eq == NULL) {
                log_err("[read_manifest] malformed MAPPING line: %s", line);
                rc = -1; goto cleanup;
            }
            *eq = '\0';
            const char *key_str   = line;
            const char *value_str = eq + 1;

            struct instance *ins = &instance_list[idx];
            int m = cur_mapping_idx;

            if (!strcmp(key_str, "after")) {
                if (seen_after) {
                    log_err("[read_manifest] duplicate 'after=' in MAPPING '%s' on '%s'",
                            mapping_names[m], ins->name[0] ? ins->name : "(unnamed)");
                    rc = -1; goto cleanup;
                }
                /* Overwrite the empty sentinel set on MAPPING entry. */
                free(scratch_after_keys[idx][m]);
                int vlen = (int) strlen(value_str);
                if (vlen > 0) {
                    scratch_after_keys[idx][m] = (char *) calloc((size_t) vlen + 1, sizeof(char));
                    memcpy(scratch_after_keys[idx][m], value_str, (size_t) vlen);
                } else {
                    scratch_after_keys[idx][m] = strdup("");
                }
                seen_after = 1;
            }
            else if (!strcmp(key_str, "before")) {
                if (seen_before) {
                    log_err("[read_manifest] duplicate 'before=' in MAPPING '%s' on '%s'",
                            mapping_names[m], ins->name[0] ? ins->name : "(unnamed)");
                    rc = -1; goto cleanup;
                }
                /* Overwrite the empty sentinel set on MAPPING entry. */
                free(scratch_before_keys[idx][m]);
                int vlen = (int) strlen(value_str);
                if (vlen > 0) {
                    scratch_before_keys[idx][m] = (char *) calloc((size_t) vlen + 1, sizeof(char));
                    memcpy(scratch_before_keys[idx][m], value_str, (size_t) vlen);
                } else {
                    scratch_before_keys[idx][m] = strdup("");
                }
                seen_before = 1;
            }
            else if (!strcmp(key_str, "mapping_behavior")) {
                if (seen_mapping_behavior) {
                    log_err("[read_manifest] duplicate 'mapping_behavior=' in MAPPING '%s' on '%s'",
                            mapping_names[m], ins->name[0] ? ins->name : "(unnamed)");
                    rc = -1; goto cleanup;
                }
                if      (!strcmp(value_str, "Ignore"))  ins->mapping_behaviors[m] = MAPPING_BEHAVIOR_IGNORE;
                else if (!strcmp(value_str, "Cascade")) ins->mapping_behaviors[m] = MAPPING_BEHAVIOR_CASCADE;
                else {
                    log_err("[read_manifest] MAPPING '%s' on '%s': invalid mapping_behavior '%s' (expected 'Ignore' or 'Cascade')",
                            mapping_names[m], ins->name[0] ? ins->name : "(unnamed)", value_str);
                    rc = -1; goto cleanup;
                }
                seen_mapping_behavior = 1;
            }
            else {
                log_err("[read_manifest] unknown MAPPING field: %s (in '%s' on '%s')",
                        key_str, mapping_names[m], ins->name[0] ? ins->name : "(unnamed)");
                rc = -1; goto cleanup;
            }
        }
        /* else: outside any block; skip blank/comment-ish lines silently. */

        end++;
        start = end;
    }

    if (state != OUTSIDE) {
        log_err("[read_manifest] file ended while still inside a block");
        rc = -1; goto cleanup;
    }

    rc = manifest_resolve_pointers(scratch_after_keys, scratch_before_keys);

cleanup:
    if (scratch_after_keys != NULL) {
        for (int i = 0; i < instance_count; i++) {
            if (scratch_after_keys[i] != NULL) {
                for (int m = 0; m < mapping_count; m++) {
                    free(scratch_after_keys[i][m]);
                }
                free(scratch_after_keys[i]);
            }
        }
        free(scratch_after_keys);
    }
    if (scratch_before_keys != NULL) {
        for (int i = 0; i < instance_count; i++) {
            if (scratch_before_keys[i] != NULL) {
                for (int m = 0; m < mapping_count; m++) {
                    free(scratch_before_keys[i][m]);
                }
                free(scratch_before_keys[i]);
            }
        }
        free(scratch_before_keys);
    }
    free(contents);
    return rc;
}

/* manager_copy_unit_files — copies every per-instance unit file from the
 * staging directory (`unit_file_staging_directory`, populated by step 4)
 * into the systemd user destination (`unit_file_destination`, typically
 * ~/.config/systemd/user/). Source and destination are both populated by
 * manager_read_config from ./etc/Launcher.config. Per-instance file names
 * are taken from `instance_list[i].unit_file_name`. Verifies each
 * destination file exists with size equal to the source after copy. */
int manager_copy_unit_files() {

    int i;
    int failures = 0;
    char source[PATH_MAX]      = {0};
    char destination[PATH_MAX] = {0};

    for (i = 0; i < instance_count; i++) {
        build_path(source,      unit_file_staging_directory,
                   instance_list[i].unit_file_name, (char *) NULL);
        build_path(destination, unit_file_destination,
                   instance_list[i].unit_file_name, (char *) NULL);

        if (check_file_exists(source) != 0) {
            log_err("[copy] source missing: %s", source);
            failures++;
            continue;
        }

        if (copy_file(source, destination) < 0) {
            log_err("[copy] copy failed: %s -> %s", source, destination);
            failures++;
            continue;
        }

        log_info("[copy] %s -> %s", source, destination);
    }

    if (failures > 0) {
        log_err("[copy] %d/%d copies failed", failures, instance_count);
        return -1;
    }

    /* Active verification — stat each destination and compare size. */
    for (i = 0; i < instance_count; i++) {
        struct stat src_st = {0};
        struct stat dst_st = {0};

        build_path(source,      unit_file_staging_directory,
                   instance_list[i].unit_file_name, (char *) NULL);
        build_path(destination, unit_file_destination,
                   instance_list[i].unit_file_name, (char *) NULL);

        if (stat(destination, &dst_st) != 0) {
            log_err("[copy] verify: missing destination %s", destination);
            return -1;
        }
        if (stat(source, &src_st) == 0 && src_st.st_size != dst_st.st_size) {
            log_err("[copy] verify: size mismatch for %s (src=%lld dst=%lld)",
                    instance_list[i].unit_file_name,
                    (long long) src_st.st_size,
                    (long long) dst_st.st_size);
            return -1;
        }
    }
    log_info("[copy] verify: %d/%d destination files present with matching size",
             instance_count, instance_count);

    return 0;
}

int manager_register_tracked_units() {

    /* Register the state-changed callback before attach fires initial
     * snapshots; otherwise the (initial) invocations are silently dropped. */
    tracked_units_set_state_changed_cb(on_unit_state_changed, NULL);

    /* Build a name array for tracked_units_register. */
    const char **names = (const char **) calloc(instance_count, sizeof(const char *));
    for (int i = 0; i < instance_count; i++) {
        names[i] = instance_list[i].unit_file_name;
    }

    int rc = tracked_units_register(names, (size_t) instance_count);
    free(names);

    if (rc < 0) {
        log_err("[register] tracked_units_register rc=%d", rc);
        return rc;
    }

    /* Link each instance to its tracked_unit. */
    for (int i = 0; i < instance_count; i++) {
        instance_list[i].tracked = tracked_unit_find_by_name(instance_list[i].unit_file_name);
        log_info("[register] %s -> slot=%ld",
                 instance_list[i].unit_file_name,
                 instance_list[i].tracked
                     ? (long) (instance_list[i].tracked - tracked_units)
                     : -1L);
    }

    /* Active verification. */
    if (tracked_units_count != (size_t) instance_count) {
        log_err("[register] verify: tracked_units_count=%zu != instance_count=%d",
                tracked_units_count, instance_count);
        return -1;
    }
    for (int i = 0; i < instance_count; i++) {
        tracked_unit *t = instance_list[i].tracked;
        if (!t) {
            log_err("[register] verify: %s has no tracked slot",
                    instance_list[i].unit_file_name);
            return -1;
        }
        long off = (long) (t - tracked_units);
        if (off < 0 || off >= (long) LIMIT_TRACKED_UNITS) {
            log_err("[register] verify: %s tracked pointer out of range (off=%ld)",
                    instance_list[i].unit_file_name, off);
            return -1;
        }
        if (!t->in_use) {
            log_err("[register] verify: %s tracked slot not in_use",
                    instance_list[i].unit_file_name);
            return -1;
        }
    }
    log_info("[register] verify: %zu/%d tracked slots in_use",
             tracked_units_count, instance_count);

    return 0;
}

int manager_capture_instances() {

    int rc;

    /* Reload first so systemd sees the newly copied unit files. */
    log_info("[capture] daemon reload (pre-enable)");
    rc = dbus_daemon_reload_sender();
    if (rc < 0) { log_err("[capture] reload rc=%d", rc); return rc; }

    /* Clear stale failures before enabling. */
    log_info("[capture] resetting failures on all tracked units");
    tracked_units_reset_failed_all();

    /* Enable each instance. */
    int enabled = 0;
    for (int i = 0; i < instance_count; i++) {
        rc = manager_operate_instance(&instance_list[i], INSTANCE_OP_ENABLE);
        if (rc < 0) {
            log_err("[capture] enable %s rc=%d",
                    instance_list[i].unit_file_name, rc);
            return rc;
        }
        log_info("[capture] enable %s -> OK", instance_list[i].unit_file_name);
        enabled++;
    }
    log_info("[capture] enabled %d/%d", enabled, instance_count);

    /* Reload again so systemd picks up the enabled state. */
    log_info("[capture] daemon reload (post-enable)");
    rc = dbus_daemon_reload_sender();
    if (rc < 0) { log_err("[capture] reload rc=%d", rc); return rc; }

    /* Attach tracked units — resolves object paths, subscribes
     * PropertiesChanged, fetches initial property snapshots. */
    log_info("[capture] attaching tracked units");
    tracked_units_attach_all();

    /* Second reset, now that LoadUnit has run. The pre-enable call above is a
     * no-op for units systemd had gc'd between sessions (NoSuchUnit). By here
     * every name resolves to a loaded object path, so ResetFailedUnit actually
     * clears any stale failed bit + start-rate-limit counter carried over
     * from a prior run. Nothing has been Start-ed by us yet (step 8). */
    log_info("[capture] resetting failures on all tracked units (post-attach)");
    tracked_units_reset_failed_all();

    /* Active verification — check every tracked unit attached and has a
     * reasonable load / unit_file state. */
    int attached      = 0;
    int state_issues  = 0;
    for (int i = 0; i < instance_count; i++) {
        tracked_unit *t = instance_list[i].tracked;
        if (!t) continue;

        if (t->attached) attached++;

        log_info("[capture] attach %s -> attached=%s object_path=%s load=%s unit_file=%s",
                 t->name,
                 t->attached ? "yes" : "no",
                 t->object_path[0] ? t->object_path : "(none)",
                 load_state_to_string(t->load_state),
                 t->unit_file_state[0] ? t->unit_file_state : "(unknown)");

        if (!t->attached) {
            log_err("[capture] verify: %s not attached", t->name);
            state_issues++;
            continue;
        }
        if (t->load_state != LOAD_STATE_LOADED) {
            log_err("[capture] verify: %s load_state=%s (want loaded)",
                    t->name, load_state_to_string(t->load_state));
            state_issues++;
        }
        if (t->unit_file_state[0] &&
            strcmp(t->unit_file_state, "enabled")         != 0 &&
            strcmp(t->unit_file_state, "enabled-runtime") != 0 &&
            strcmp(t->unit_file_state, "static")          != 0 &&
            strcmp(t->unit_file_state, "linked")          != 0 &&
            strcmp(t->unit_file_state, "linked-runtime")  != 0) {
            log_err("[capture] verify: %s unit_file_state=%s "
                    "(want enabled / enabled-runtime / static / linked / linked-runtime)",
                    t->name, t->unit_file_state);
            state_issues++;
        }
    }

    log_info("[capture] verify: %d/%d attached, %d state anomalies",
             attached, instance_count, state_issues);

    if (attached != instance_count || state_issues > 0) {
        log_err("[capture] verify: capture incomplete");
        return -1;
    }

    return 0;
}

int manager_operate_instance(struct instance *ins, enum instance_operation op) {

    int retval = 0;

    switch (op) {

        case INSTANCE_OP_ENABLE:
            retval = dbus_enable_unit(ins->unit_file_name);
            break;

        case INSTANCE_OP_DISABLE:
            retval = dbus_disable_unit(ins->unit_file_name);
            break;

        case INSTANCE_OP_START:
            retval = dbus_start_unit(ins->unit_file_name, "replace");
            break;

        case INSTANCE_OP_STOP:
            retval = dbus_stop_unit(ins->unit_file_name, "replace");
            break;

        case INSTANCE_OP_RESTART:
            retval = dbus_restart_unit(ins->unit_file_name);
            break;

        case INSTANCE_OP_RELOAD:
            retval = dbus_reload_unit(ins->unit_file_name);
            break;

        case INSTANCE_OP_GET_UNIT:
        case INSTANCE_OP_CHECK_STATE:
            /* Handled by the tracked_unit subsystem. No-op here. */
            break;
    }

    return retval;
}

int manager_run() {

    /* Phase 1 — issue StartUnit for every registered instance. Folded in
     * from the former manager_start_instances so the spine has a single
     * "run" primitive (start + wait); the standalone start function no
     * longer exists. */
    int started = 0;
    int errors  = 0;

    log_info("[run] starting %d instances", instance_count);

    for (int i = 0; i < instance_count; i++) {
        int rc = manager_operate_instance(&instance_list[i], INSTANCE_OP_START);
        if (rc < 0) {
            log_err("[run] start %s rc=%d", instance_list[i].unit_file_name, rc);
            errors++;
        } else {
            log_info("[run] start %s -> OK", instance_list[i].unit_file_name);
            started++;

            /* Provisional deadline, anchored at StartUnit issue time. A unit
             * whose start job is cancelled by a failed Requires= dependency
             * never enters ACTIVATING and emits no unit state change, so the
             * ACTIVATING-anchored arming below never happens and the wait
             * loops would spin on it forever. Re-armed (once) from the
             * precise first-ACTIVATING timestamp in on_unit_state_changed. */
            if (instance_list[i].deadline_usec == 0)
                arm_start_deadline(&instance_list[i], now_monotonic_usec(),
                                   "provisional at StartUnit");
        }
    }

    log_info("[run] start phase: %d/%d started, errors=%d",
             started, instance_count, errors);

    if (errors > 0) return -1;

    /* Phase 2 — dispatch to the settled-condition wait loop per the
     * configured operation_mode (set by PROGRAM_MODE at parse time). */
    switch (operation_mode) {

        case OPERATION_MODE_BASIC:
            return manager_run_basic();

        case OPERATION_MODE_MONITOR:
            return manager_run_monitor();

        default:
            log_err("[run] unknown operation_mode=%d", (int) operation_mode);
            return -1;
    }
}

/* manager_run_basic — BASIC mode body. Waits until every instance has
 * reached its settled phase per the file-scope basic_termination_condition
 * (TERM_ALL_FINISHED today). Returns 0 iff every settled instance is in a
 * SUCCEEDED-* phase; -1 if any settled instance is in a FAILED-* phase.
 *
 * Hang protection: per-unit deadline derived from the unit's own
 * TimeoutStartUSec * (StartLimitBurst for Restart units, 1 for Abort units).
 * Set in on_unit_state_changed on first ACTIVATING. When exceeded while
 * still INTERMEDIATE, the unit's phase is moved to FAILED_ABORT.
 *
 * Instances stay running under systemd after we return — manager only
 * disconnects from the bus in step 10 (tear_down). */
int manager_run_basic() {

    const enum termination_condition cond = basic_termination_condition;

    log_info("[run] mode=BASIC, exit policy=ALL_FINISHED, waiting for %d instance(s) to settle",
             instance_count);

    /* Initial pass — events delivered during the step 8 → step 9 boundary may
     * already have arrived through on_unit_state_changed. Re-derive the phase
     * from the current tracked snapshot too, in case the callback was called
     * before tracked-unit pointers were complete. */
    for (int i = 0; i < instance_count; i++) {
        instance_list[i].lifecycle_phase = phase_of(&instance_list[i]);
    }

    while (1) {

        uint64_t now = now_monotonic_usec();

        /* Deadline reassessment: any INTERMEDIATE/FAILED_INTERMEDIATE
         * instance past its armed deadline becomes FAILED_ABORT. */
        for (int i = 0; i < instance_count; i++) {
            struct instance *ins = &instance_list[i];
            enum unit_phase  c   = ins->lifecycle_phase;

            if (c != UNIT_PHASE_INTERMEDIATE && c != UNIT_PHASE_FAILED_INTERMEDIATE) continue;
            if (ins->deadline_usec == 0 || ins->deadline_usec == UINT64_MAX) continue;

            if (now > ins->deadline_usec) {
                log_err("[run] deadline exceeded for %s — moving phase to FAILED_ABORT",
                        ins->unit_file_name);
                ins->lifecycle_phase = UNIT_PHASE_FAILED_ABORT;
            }
        }

        /* Immediate-abort check (TERM_ALL_FINISHED makes this a no-op; kept
         * for policy generality). */
        for (int i = 0; i < instance_count; i++) {
            struct instance *ins = &instance_list[i];
            if (is_disqualifying(ins->lifecycle_phase, cond)) {
                log_err("[run] %s in phase %s — aborting per policy",
                        ins->unit_file_name,
                        unit_phase_to_string(ins->lifecycle_phase));
                return -1;
            }
        }

        /* All-settled check. */
        int settled    = 0;
        int succeeded  = 0;
        int failed     = 0;
        for (int i = 0; i < instance_count; i++) {
            enum unit_phase c = instance_list[i].lifecycle_phase;
            if (!is_settled(c, cond)) continue;
            settled++;
            if (c == UNIT_PHASE_SUCCEEDED_RUNNING ||
                c == UNIT_PHASE_SUCCEEDED_EXITED  ||
                c == UNIT_PHASE_SUCCEEDED_STOPPED)
                succeeded++;
            else
                failed++;
        }

        if (settled == instance_count) {
            log_info("[run] settled — succeeded=%d failed=%d", succeeded, failed);
            return failed > 0 ? -1 : 0;
        }

        /* Pump one cycle. Wait up to 1s so the deadline check at the top of
         * the next iteration runs in a timely manner even if no D-Bus event
         * arrives. dbus_process is drained to empty before re-checking. */
        dbus_wait(1000000ULL);
        while (dbus_process(NULL) > 0) { /* drain */ }
    }
}

/* manager_run_monitor — MONITOR mode body. Same loop shape as
 * manager_run_basic but with monitor_termination_condition: SUCCEEDED_RUNNING
 * is NOT settled (a still-running daemon hasn't terminated yet), and
 * SUCCEEDED_STOPPED is. Returns 0 iff every settled instance is in
 * {SUCCEEDED_EXITED, SUCCEEDED_STOPPED}, -1 otherwise.
 *
 * Hang protection is unchanged: the per-unit deadline armed in
 * on_unit_state_changed still applies — INTERMEDIATE/FAILED_INTERMEDIATE
 * units past their deadline become FAILED_ABORT and settle.
 *
 * Caveat: a unit that legitimately stays SUCCEEDED_RUNNING indefinitely
 * (long-running daemon, no external stop) will hold the manager in this
 * loop forever. SIGTERM remains the escape. */
int manager_run_monitor() {

    const enum termination_condition cond = monitor_termination_condition;

    log_info("[run] mode=MONITOR, exit policy=ALL_TERMINATED, waiting for %d instance(s) to terminate",
             instance_count);

    /* Initial pass — same rationale as BASIC: events delivered during the
     * step 8 → step 9 boundary may already have arrived via on_unit_state_changed. */
    for (int i = 0; i < instance_count; i++) {
        instance_list[i].lifecycle_phase = phase_of(&instance_list[i]);
    }

    while (1) {

        uint64_t now = now_monotonic_usec();

        /* Deadline reassessment: any INTERMEDIATE/FAILED_INTERMEDIATE
         * instance past its armed deadline becomes FAILED_ABORT. */
        for (int i = 0; i < instance_count; i++) {
            struct instance *ins = &instance_list[i];
            enum unit_phase  c   = ins->lifecycle_phase;

            if (c != UNIT_PHASE_INTERMEDIATE && c != UNIT_PHASE_FAILED_INTERMEDIATE) continue;
            if (ins->deadline_usec == 0 || ins->deadline_usec == UINT64_MAX) continue;

            if (now > ins->deadline_usec) {
                log_err("[run] deadline exceeded for %s — moving phase to FAILED_ABORT",
                        ins->unit_file_name);
                ins->lifecycle_phase = UNIT_PHASE_FAILED_ABORT;
            }
        }

        /* Immediate-abort check (TERM_MONITOR_ALL_TERMINATED is no-op; kept
         * for policy generality). */
        for (int i = 0; i < instance_count; i++) {
            struct instance *ins = &instance_list[i];
            if (is_disqualifying(ins->lifecycle_phase, cond)) {
                log_err("[run] %s in phase %s — aborting per policy",
                        ins->unit_file_name,
                        unit_phase_to_string(ins->lifecycle_phase));
                return -1;
            }
        }

        /* All-settled check. Success = SUCCEEDED_EXITED or SUCCEEDED_STOPPED
         * (RUNNING isn't settled here, so it never enters the tally). */
        int settled    = 0;
        int succeeded  = 0;
        int failed     = 0;
        for (int i = 0; i < instance_count; i++) {
            enum unit_phase c = instance_list[i].lifecycle_phase;
            if (!is_settled(c, cond)) continue;
            settled++;
            if (c == UNIT_PHASE_SUCCEEDED_EXITED ||
                c == UNIT_PHASE_SUCCEEDED_STOPPED)
                succeeded++;
            else
                failed++;
        }

        if (settled == instance_count) {
            log_info("[run] settled — succeeded=%d failed=%d", succeeded, failed);
            return failed > 0 ? -1 : 0;
        }

        dbus_wait(1000000ULL);
        while (dbus_process(NULL) > 0) { /* drain */ }
    }
}

int manager_terminate_instances() {

    int stopped = 0;
    int errors  = 0;

    log_info("[terminate] stopping %d instances", instance_count);

    /* Issue STOP for every instance. systemd handles them asynchronously;
     * the wait loop below blocks until each unit reaches a terminal phase. */
    for (int i = 0; i < instance_count; i++) {
        int rc = manager_operate_instance(&instance_list[i], INSTANCE_OP_STOP);
        if (rc < 0) {
            log_err("[terminate] %s rc=%d", instance_list[i].unit_file_name, rc);
            errors++;
        } else {
            log_info("[terminate] %s -> OK", instance_list[i].unit_file_name);
            stopped++;
        }
    }

    log_info("[terminate] issued %d stop request(s) (errors=%d), waiting for termination",
             stopped, errors);

    /* Wait until every instance reaches a terminal phase. A unit that was
     * never active (was_active=false) has nothing to wait for and is
     * treated as already-terminated here, so partial-failure cleanup
     * paths (e.g. step 7 failed before any unit started) don't hang. */
    const uint64_t deadline_usec = now_monotonic_usec() + 60ULL * 1000000ULL;
    int all_terminated = 0;

    while (1) {
        uint64_t now = now_monotonic_usec();

        /* Re-derive phases from the current tracked snapshot in case any
         * PropertiesChanged arrived between the last on_unit_state_changed
         * fire and this iteration. */
        for (int i = 0; i < instance_count; i++) {
            instance_list[i].lifecycle_phase = phase_of(&instance_list[i]);
        }

        int terminated = 0;
        for (int i = 0; i < instance_count; i++) {
            enum unit_phase c = instance_list[i].lifecycle_phase;
            if (c == UNIT_PHASE_SUCCEEDED_STOPPED ||
                c == UNIT_PHASE_SUCCEEDED_EXITED  ||
                c == UNIT_PHASE_FAILED_ABORT      ||
                c == UNIT_PHASE_FAILED_BURST) {
                terminated++;
                continue;
            }
            if (!instance_list[i].was_active) {
                terminated++;
            }
        }

        if (terminated == instance_count) {
            all_terminated = 1;
            break;
        }

        if (now > deadline_usec) {
            log_err("[terminate] wait deadline exceeded — %d/%d terminated",
                    terminated, instance_count);
            break;
        }

        dbus_wait(1000000ULL);
        while (dbus_process(NULL) > 0) { /* drain */ }
    }

    log_info("[terminate] verify: %d/%d stopped, errors=%d, %s",
             stopped, instance_count, errors,
             all_terminated ? "all terminated" : "termination INCOMPLETE");

    return (errors > 0 || !all_terminated) ? -1 : 0;
}

/* ---------------------------------------------------------------------------
 * Lifecycle phase helpers — used by on_unit_state_changed and
 * manager_run_basic to drive the BASIC settled-state loop.
 *
 * phase_of()         : maps live tracked_unit + struct instance state to one
 *                      of the six unit_phase values per the plan's state
 *                      table.
 * is_settled()       : true iff a phase counts as "settled" under the chosen
 *                      termination_condition. SUCCEEDED-* always counts;
 *                      FAILED-abort/burst count depending on policy.
 * is_disqualifying() : true iff a phase is a non-settled FAILURE under the
 *                      chosen termination_condition — i.e. an immediate-abort
 *                      trigger. Always 0 under TERM_ALL_FINISHED.
 * now_monotonic_usec : CLOCK_MONOTONIC in microseconds. Used for
 *                      first_activating_usec and deadline_usec arithmetic.
 * unit_phase_to_string: for log lines.
 * find_instance_by_tracked: linear lookup of struct instance * by tracked_unit*.
 * --------------------------------------------------------------------------- */

static uint64_t now_monotonic_usec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t) ts.tv_sec * 1000000ULL + (uint64_t) ts.tv_nsec / 1000ULL;
}

static enum unit_phase phase_of(const struct instance *ins) {
    const tracked_unit *t = ins ? ins->tracked : NULL;
    if (!t) return UNIT_PHASE_INTERMEDIATE;

    switch (t->active_state) {
        case ACTIVE_STATE_ACTIVE:
            if (!strcmp(t->sub_state, "running")) return UNIT_PHASE_SUCCEEDED_RUNNING;
            if (!strcmp(t->sub_state, "exited"))  return UNIT_PHASE_SUCCEEDED_EXITED;
            return UNIT_PHASE_INTERMEDIATE;
        case ACTIVE_STATE_INACTIVE:
            /* inactive/dead with Result=success after the unit was once up =
             * "ran clean then stopped". Distinguished from "never started"
             * (same systemd state, was_active=false) which stays intermediate. */
            if (!strcmp(t->sub_state, "dead") &&
                (t->result[0] == '\0' || !strcmp(t->result, "success")) &&
                ins->was_active) {
                return UNIT_PHASE_SUCCEEDED_STOPPED;
            }
            return UNIT_PHASE_INTERMEDIATE;
        case ACTIVE_STATE_FAILED:
            if (!strcmp(t->result, "start-limit-hit")) return UNIT_PHASE_FAILED_BURST;
            if (ins->failure_behavior == FAILURE_BEHAVIOR_RESTART)
                return UNIT_PHASE_FAILED_INTERMEDIATE;
            return UNIT_PHASE_FAILED_ABORT;
        default:
            return UNIT_PHASE_INTERMEDIATE;
    }
}

static int is_settled(enum unit_phase c, enum termination_condition cond) {
    switch (cond) {
        case TERM_ALL_SUCCEEDED:
            return c == UNIT_PHASE_SUCCEEDED_RUNNING ||
                   c == UNIT_PHASE_SUCCEEDED_EXITED  ||
                   c == UNIT_PHASE_SUCCEEDED_STOPPED;
        case TERM_ALL_FINISHED:
            return c == UNIT_PHASE_SUCCEEDED_RUNNING ||
                   c == UNIT_PHASE_SUCCEEDED_EXITED  ||
                   c == UNIT_PHASE_SUCCEEDED_STOPPED ||
                   c == UNIT_PHASE_FAILED_ABORT     ||
                   c == UNIT_PHASE_FAILED_BURST;
        case TERM_MONITOR_ALL_TERMINATED:
            /* RUNNING is NOT settled here — the daemon is still going. */
            return c == UNIT_PHASE_SUCCEEDED_EXITED  ||
                   c == UNIT_PHASE_SUCCEEDED_STOPPED ||
                   c == UNIT_PHASE_FAILED_ABORT     ||
                   c == UNIT_PHASE_FAILED_BURST;
    }
    return 0;
}

static int is_disqualifying(enum unit_phase c, enum termination_condition cond) {
    switch (cond) {
        case TERM_ALL_SUCCEEDED:
            return c == UNIT_PHASE_FAILED_ABORT || c == UNIT_PHASE_FAILED_BURST;
        case TERM_ALL_FINISHED:
        case TERM_MONITOR_ALL_TERMINATED:
            return 0;
    }
    return 0;
}

static const char *unit_phase_to_string(enum unit_phase c) {
    switch (c) {
        case UNIT_PHASE_INTERMEDIATE:        return "intermediate";
        case UNIT_PHASE_FAILED_INTERMEDIATE: return "failed-intermediate";
        case UNIT_PHASE_SUCCEEDED_RUNNING:   return "succeeded-running";
        case UNIT_PHASE_SUCCEEDED_EXITED:    return "succeeded-exited";
        case UNIT_PHASE_SUCCEEDED_STOPPED:   return "succeeded-stopped";
        case UNIT_PHASE_FAILED_ABORT:        return "failed-abort";
        case UNIT_PHASE_FAILED_BURST:        return "failed-burst";
    }
    return "?";
}

static struct instance *find_instance_by_tracked(const tracked_unit *u) {
    if (!u || !instance_list) return NULL;
    for (int i = 0; i < instance_count; i++) {
        if (instance_list[i].tracked == u) return &instance_list[i];
    }
    return NULL;
}

/* arm_start_deadline — set ins->deadline_usec to anchor_usec plus the unit's
 * start budget: TimeoutStartUSec × StartLimitBurst for Restart instances,
 * TimeoutStartUSec × 1 for Abort instances. TimeoutStartUSec=infinity
 * propagates as "no deadline" (UINT64_MAX); 0 (property read failed) falls
 * back to systemd's documented 90s default. */
static void arm_start_deadline(struct instance *ins, uint64_t anchor_usec, const char *why) {

    const tracked_unit *u = ins->tracked;

    uint64_t timeout = u ? u->timeout_start_usec : 0;
    uint32_t burst   = u ? u->start_limit_burst  : 0;
    uint32_t budget  = (ins->failure_behavior == FAILURE_BEHAVIOR_RESTART)
                          ? (burst > 0 ? burst : 5)  /* 0 = "no limit"; treat as our default 5 */
                          : 1;

    if (timeout == UINT64_MAX) {
        ins->deadline_usec = UINT64_MAX;
        log_info("[run] deadline for %s = infinity (TimeoutStartUSec=infinity, %s)",
                 ins->unit_file_name, why);
        return;
    }
    if (timeout == 0) timeout = 90ULL * 1000000ULL;

    ins->deadline_usec = anchor_usec + (uint64_t) budget * timeout;
    log_info("[run] deadline for %s armed (%s): budget=%u × timeout=%llu us "
             "= %llu us from anchor",
             ins->unit_file_name, why, budget,
             (unsigned long long) timeout,
             (unsigned long long)((uint64_t) budget * timeout));
}

/* on_unit_state_changed — invoked by the tracker whenever a tracked
 * property is updated via a PropertiesChanged signal, plus once per
 * tracked unit during the initial-snapshot pass (property_name ==
 * "(initial)"). Adapted from __old_source: the termination-counter
 * branch is dropped because the new schema no longer carries
 * INSTANCE_ENTRY discrimination or a running_entries global. */
static void on_unit_state_changed(const tracked_unit *u,
                                  const char *property_name,
                                  void *userdata) {

    (void) userdata;

    if (!u) return;

    /* Act on ActiveState, SubState, Result, and the initial snapshot. Result
     * matters because failed/failed → failed/failed with Result=start-limit-hit
     * is a Result-only transition (ActiveState/SubState don't change). */
    if (strcmp(property_name, "ActiveState") != 0 &&
        strcmp(property_name, "SubState")    != 0 &&
        strcmp(property_name, "Result")      != 0 &&
        strcmp(property_name, "(initial)")   != 0) {
        return;
    }

    log_info("[StateChanged] unit=%-30s active=%-12s sub=%-12s result=%s",
             u->name,
             active_state_to_string(u->active_state),
             u->sub_state,
             u->result[0] ? u->result : "(unset)");

    /* Find the owning struct instance so we can update lifecycle bookkeeping.
     * Linear scan is fine — instance_count is small (handful of units). */
    struct instance *ins = find_instance_by_tracked(u);
    if (!ins) return;

    /* Capture first-activating timestamp + arm the per-unit deadline on the
     * first transition into ACTIVATING. Overwrites the provisional deadline
     * armed at StartUnit time in manager_run (this anchor is the precise
     * one); after that, deadline is frozen — this block runs once, gated by
     * first_activating_usec. */
    if (ins->first_activating_usec == 0 &&
        u->active_state == ACTIVE_STATE_ACTIVATING) {
        ins->first_activating_usec = now_monotonic_usec();
        arm_start_deadline(ins, ins->first_activating_usec, "first ACTIVATING");
    }

    /* Latch was_active on the first observed ACTIVE so phase_of can map a
     * later inactive/dead to SUCCEEDED_STOPPED rather than INTERMEDIATE. */
    if (u->active_state == ACTIVE_STATE_ACTIVE) ins->was_active = true;

    /* Always recompute the phase — every Settled/Intermediate decision flows
     * from this single helper, called consistently here and in the BASIC loop. */
    enum unit_phase prev = ins->lifecycle_phase;
    ins->lifecycle_phase = phase_of(ins);
    if (prev != ins->lifecycle_phase) {
        log_info("[phase]  unit=%-30s %s -> %s",
                 u->name,
                 unit_phase_to_string(prev),
                 unit_phase_to_string(ins->lifecycle_phase));
    }
}

/* ---------------------------------------------------------------------------
 * Debug / logger functions
 * --------------------------------------------------------------------------- */

int logger_init(const char *log_dir) {

    if (!log_dir || !log_dir[0]) return -1;

    char path[PATH_MAX] = {0};
    if (build_path(path, log_dir, "manager.log", (char *) NULL) != 0) return -1;

    manager_log = fopen(path, "a");
    if (!manager_log) {
        fprintf(stderr, "[logger] cannot open %s: %s\n", path, strerror(errno));
        return -1;
    }

    /* Session header so successive runs are visually separable. */
    time_t     now = time(NULL);
    struct tm  tm_local;
    localtime_r(&now, &tm_local);
    fprintf(manager_log,
            "\n================================================================================\n"
            " Manager session start %04d-%02d-%02d %02d:%02d:%02d\n"
            "================================================================================\n",
            tm_local.tm_year + 1900, tm_local.tm_mon + 1, tm_local.tm_mday,
            tm_local.tm_hour,        tm_local.tm_min,     tm_local.tm_sec);
    fflush(manager_log);

    return 0;
}

void logger_tear_down(void) {
    if (manager_log) {
        fflush(manager_log);
        fclose(manager_log);
        manager_log = NULL;
    }
}

static void log_timestamp(char *buf, size_t bufsz) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    struct tm tm_local;
    localtime_r(&tv.tv_sec, &tm_local);
    snprintf(buf, bufsz, "%04d-%02d-%02d %02d:%02d:%02d.%03d",
             tm_local.tm_year + 1900, tm_local.tm_mon + 1, tm_local.tm_mday,
             tm_local.tm_hour,        tm_local.tm_min,     tm_local.tm_sec,
             (int) (tv.tv_usec / 1000));
}

void log_info(const char *fmt, ...) {
    va_list ap;

    va_start(ap, fmt);
    vprintf(fmt, ap);
    putchar('\n');
    va_end(ap);
    fflush(stdout);

    if (manager_log) {
        char ts[32];
        log_timestamp(ts, sizeof(ts));
        fprintf(manager_log, "[%s] ", ts);
        va_start(ap, fmt);
        vfprintf(manager_log, fmt, ap);
        va_end(ap);
        fputc('\n', manager_log);
        fflush(manager_log);
    }
}

void log_err(const char *fmt, ...) {
    va_list ap;

    fprintf(stderr, "[ERROR] ");
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);

    if (manager_log) {
        char ts[32];
        log_timestamp(ts, sizeof(ts));
        fprintf(manager_log, "[%s] [ERROR] ", ts);
        va_start(ap, fmt);
        vfprintf(manager_log, fmt, ap);
        va_end(ap);
        fputc('\n', manager_log);
        fflush(manager_log);
    }
}

void step_begin(int idx, const char *step_name) {
    clock_gettime(CLOCK_MONOTONIC, &step_start_time);
    step_start_offset_us = (step_start_time.tv_sec  - session_start_time.tv_sec)  * 1000000LL
                         + (step_start_time.tv_nsec - session_start_time.tv_nsec) / 1000LL;
    log_info("%s", "");
    log_info("========================================");
    log_info(" [%2d/%d] %s", idx, STEP_TOTAL, step_name);
    log_info("========================================");
}

void step_end(int idx, const char *step_name, int rc) {
    struct timespec end_time;
    clock_gettime(CLOCK_MONOTONIC, &end_time);
    long long elapsed_us = (end_time.tv_sec  - step_start_time.tv_sec)  * 1000000LL
                         + (end_time.tv_nsec - step_start_time.tv_nsec) / 1000LL;
    long long t_end_us   = (end_time.tv_sec  - session_start_time.tv_sec)  * 1000000LL
                         + (end_time.tv_nsec - session_start_time.tv_nsec) / 1000LL;
    long elapsed_ms = (long) (elapsed_us / 1000LL);
    if (rc < 0) {
        log_err("[%s] FAIL (rc=%d, %ld ms)", step_name, rc, elapsed_ms);
    } else {
        log_info("[%s] OK (%ld ms)", step_name, elapsed_ms);
    }
    printf("TIMING|c|%d|%s|%lld|%lld|%lld|%d\n",
           idx, step_name, step_start_offset_us, t_end_us, elapsed_us, rc);
    fflush(stdout);
}

void debug_print_instances(int fd, const char *dump_file_name) {

    int i, j;
    int is_stream;
    int out;

    /* -1 is the sentinel meaning "open a log file in log_directory".
     * Any non-negative value is used directly as a file descriptor.
     * STDOUT_FILENO, STDERR_FILENO, and STDIN_FILENO are the standard
     * stream descriptors and are treated as stream targets. */
    is_stream = (fd != -1);

    if (is_stream) {
        out = fd;
    } else {
        char log_path[PATH_MAX] = {0};
        if (build_path(log_path, log_directory, dump_file_name, (char *) NULL) != 0) {
            dprintf(STDERR_FILENO, "debug_print_instances: failed to build log path.\n");
            return;
        }
        out = open(log_path, O_WRONLY | O_CREAT | O_TRUNC, S_IRWXU);
        if (out == -1) {
            dprintf(STDERR_FILENO, "debug_print_instances: cannot open log file: %s\n", log_path);
            return;
        }
    }

    /* Header */
    dprintf(out, "=========================\n");
    dprintf(out, "  %s\n", dump_file_name ? dump_file_name : "Instance dump");
    dprintf(out, "  Instances: %d\n", instance_count);
    dprintf(out, "=========================\n");

    for (i = 0; i < instance_count; i++) {

        struct instance *ins = &instance_list[i];

        dprintf(out, "\n  [#%d]\n", i);

        /* --- Scalar identity fields --- */
        dprintf(out, "    name              : %s\n", ins->name);
        dprintf(out, "    unit_file_name    : %s\n", ins->unit_file_name);
        dprintf(out, "    type              : %s\n",
                ins->type[0] ? ins->type : "(unspecified)");
        dprintf(out, "    path              : %s\n", ins->path);
        dprintf(out, "    command           : %s\n", ins->command);
        dprintf(out, "    failure_behavior  : %s\n",
                failure_behavior_to_string(ins->failure_behavior));

        /* --- Hierarchical mappings --- */
        if (mapping_count == 0 ||
            (ins->after_lengths == NULL && ins->before_lengths == NULL)) {
            dprintf(out, "    mappings          : (none)\n");
        } else {
            int m;
            for (m = 0; m < mapping_count; m++) {
                const char *map_name = (mapping_names && mapping_names[m])
                                       ? mapping_names[m] : "(unnamed)";
                dprintf(out, "    %-18s: after=", map_name);
                int alen = ins->after_lengths ? ins->after_lengths[m] : 0;
                if (alen == 0 || ins->after_list == NULL || ins->after_list[m] == NULL) {
                    dprintf(out, "(none)");
                } else {
                    for (j = 0; j < alen; j++) {
                        dprintf(out, " %s",
                                ins->after_list[m][j]
                                    ? ins->after_list[m][j]->name
                                    : "(unresolved)");
                    }
                }
                dprintf(out, " | before=");
                int blen = ins->before_lengths ? ins->before_lengths[m] : 0;
                if (blen == 0 || ins->before_list == NULL || ins->before_list[m] == NULL) {
                    dprintf(out, "(none)");
                } else {
                    for (j = 0; j < blen; j++) {
                        dprintf(out, " %s",
                                ins->before_list[m][j]
                                    ? ins->before_list[m][j]->name
                                    : "(unresolved)");
                    }
                }
                if (ins->mapping_behaviors) {
                    dprintf(out, " | %s",
                            mapping_behavior_to_string(ins->mapping_behaviors[m]));
                }
                dprintf(out, "\n");
            }
        }

        /* --- Runtime state fields (from tracked_unit) --- */
        if (ins->tracked) {
            tracked_unit *t = ins->tracked;
            dprintf(out, "    attached          : %s\n", t->attached ? "yes" : "no");
            dprintf(out, "    object_path       : %s\n",
                    t->object_path[0] ? t->object_path : "(not captured)");
            dprintf(out, "    load_state        : %s\n", load_state_to_string(t->load_state));
            dprintf(out, "    active_state      : %s\n", active_state_to_string(t->active_state));
            dprintf(out, "    sub_state         : %s\n",
                    t->sub_state[0] ? t->sub_state : "(unknown)");
            dprintf(out, "    unit_file_state   : %s\n",
                    t->unit_file_state[0] ? t->unit_file_state : "(unknown)");
            dprintf(out, "    exec_main_pid     : %u\n", t->exec_main_pid);
            dprintf(out, "    exec_main_status  : %d\n", t->exec_main_status);
            dprintf(out, "    result            : %s\n",
                    t->result[0] ? t->result : "(unknown)");
        } else {
            dprintf(out, "    tracked           : (not registered)\n");
        }
    }

    dprintf(out, "\n=========================\n");

    if (!is_stream) {
        close(out);
    }
}
/* ---------------------------------------------------------------------------
 * Utility functions
 * --------------------------------------------------------------------------- */
/* path_resolver — expand `~` to $HOME and resolve to an absolute path via
 * realpath. If realpath fails (e.g. the target does not yet exist), the
 * expanded path is stored verbatim instead. The output buffer must be at
 * least PATH_MAX bytes.
 *
 * Returns 0 on success, -1 on error (currently only when path starts with
 * `~` and HOME is unset). */

int path_resolver(char *abspath, const char *path) {

    char expanded[PATH_MAX] = {0};

    if (path[0] == '~') {

        const char *home = getenv("HOME");

        if (home == NULL) {
            fprintf(stderr, "path_resolver: HOME environment variable is not set.\n");
            return -1;
        }

        snprintf(expanded, PATH_MAX, "%s%s", home, path + 1);

    } else {

        strncpy(expanded, path, PATH_MAX - 1);

    }

    if (realpath(expanded, abspath) == NULL) {
        strncpy(abspath, expanded, PATH_MAX - 1);
    }

    return 0;
}
/* build_path — constructs a path from variadic string segments into out.
 *
 * Usage:
 *   build_path(out, "segment1", "segment2", ..., (char *) NULL);
 *
 * Rules:
 *   - The first parameter is the output buffer (char *, at least PATH_MAX).
 *   - Subsequent parameters are path segments (const char *).
 *   - The argument list must be terminated with (char *) NULL.
 *   - If a segment already ends with '/', the next segment is appended
 *     directly. Otherwise a '/' separator is inserted between segments.
 *   - If out would exceed PATH_MAX the function stops and returns -1.
 *
 * Returns 0 on success, -1 on error. */

int build_path(char *out, ...) {

    if (out == NULL) return -1;

    va_list args;
    va_start(args, out);

    out[0] = '\0';
    size_t len = 0;

    const char *seg = va_arg(args, const char *);

    while (seg != NULL) {

        size_t seg_len = strlen(seg);

        if (seg_len == 0) {
            seg = va_arg(args, const char *);
            continue;
        }

        if (len > 0 && out[len - 1] != '/' && seg[0] != '/') {
            if (len + 1 >= PATH_MAX) {
                va_end(args);
                fprintf(stderr, "build_path: path exceeds PATH_MAX.\n");
                return -1;
            }
            out[len++] = '/';
            out[len]   = '\0';
        }

        if (len + seg_len >= PATH_MAX) {
            va_end(args);
            fprintf(stderr, "build_path: path exceeds PATH_MAX.\n");
            return -1;
        }

        strncpy(&out[len], seg, PATH_MAX - len - 1);
        len += seg_len;

        seg = va_arg(args, const char *);
    }

    va_end(args);
    return 0;
}

int mkdir_p(const char *path) {

    if (path == NULL) return -1;

    size_t len = strnlen(path, PATH_MAX);
    if (len == 0 || len >= PATH_MAX) return -1;

    char buf[PATH_MAX];
    memcpy(buf, path, len + 1);

    if (len > 1 && buf[len - 1] == '/') buf[--len] = '\0';

    for (size_t i = 1; i <= len; i++) {
        if (buf[i] == '/' || buf[i] == '\0') {
            char saved = buf[i];
            buf[i] = '\0';
            if (mkdir(buf, 0755) != 0 && errno != EEXIST) {
                return -1;
            }
            buf[i] = saved;
        }
    }
    return 0;
}

int create_results_folder() {

    struct stat st = {0};

    if (stat(results_folder, &st) == -1) {
        if (mkdir_p(results_folder) != 0) {
            log_err("[results_folder] mkdir %s failed: %s",
                    results_folder, strerror(errno));
            return -1;
        }
        log_info("[results_folder] created %s", results_folder);
    } else {
        log_info("[results_folder] already exists: %s", results_folder);
    }

    /* Active verification. */
    if (stat(results_folder, &st) != 0 || !S_ISDIR(st.st_mode)) {
        log_err("[results_folder] verify: not a directory: %s", results_folder);
        return -1;
    }
    if (access(results_folder, W_OK) != 0) {
        log_err("[results_folder] verify: not writable: %s (%s)",
                results_folder, strerror(errno));
        return -1;
    }
    log_info("[results_folder] verify: directory exists and is writable");

    return 0;
}

int create_unit_file_dump_dir() {

    struct stat st = {0};

    if (stat(unit_file_staging_directory, &st) == -1) {
        if (mkdir_p(unit_file_staging_directory) != 0) {
            log_err("[dump_dir] mkdir %s failed: %s",
                    unit_file_staging_directory, strerror(errno));
            return -1;
        }
        log_info("[dump_dir] created %s", unit_file_staging_directory);
    } else {
        log_info("[dump_dir] already exists: %s", unit_file_staging_directory);
    }

    /* Active verification. */
    if (stat(unit_file_staging_directory, &st) != 0 || !S_ISDIR(st.st_mode)) {
        log_err("[dump_dir] verify: not a directory: %s", unit_file_staging_directory);
        return -1;
    }
    if (access(unit_file_staging_directory, W_OK) != 0) {
        log_err("[dump_dir] verify: not writable: %s (%s)",
                unit_file_staging_directory, strerror(errno));
        return -1;
    }
    log_info("[dump_dir] verify: directory exists and is writable");

    return 0;
}

int create_log_dir() {

    struct stat st = {0};

    if (stat(log_directory, &st) == -1) {
        if (mkdir(log_directory, 0744) != 0) {
            log_err("[log_dir] mkdir %s failed: %s",
                    log_directory, strerror(errno));
            return -1;
        }
        log_info("[log_dir] created %s", log_directory);
    } else {
        log_info("[log_dir] already exists: %s", log_directory);
    }

    /* Active verification. */
    if (stat(log_directory, &st) != 0 || !S_ISDIR(st.st_mode)) {
        log_err("[log_dir] verify: not a directory: %s", log_directory);
        return -1;
    }
    if (access(log_directory, W_OK) != 0) {
        log_err("[log_dir] verify: not writable: %s (%s)",
                log_directory, strerror(errno));
        return -1;
    }
    log_info("[log_dir] verify: directory exists and is writable");

    return 0;
}

/* create_runtime_dirs — single entry point that creates every runtime
 * directory the manager needs. Order matters: results_folder is created
 * first because unit_file_staging_directory may live underneath it. */
int create_runtime_dirs() {

    int rc;

    rc = create_results_folder();
    if (rc < 0) return rc;

    rc = create_unit_file_dump_dir();
    if (rc < 0) return rc;

    rc = create_log_dir();
    if (rc < 0) return rc;

    return 0;
}

int copy_file(const char *source, const char *destination) {

    int fd_source = open(source, O_RDONLY);
    if (fd_source < 0) {
        log_err("[copy_file] open source '%s' failed: %s",
                source, strerror(errno));
        return -1;
    }

    int fd_dest = open(destination, O_WRONLY | O_CREAT | O_TRUNC, S_IRWXU);
    if (fd_dest < 0) {
        log_err("[copy_file] open dest '%s' failed: %s",
                destination, strerror(errno));
        close(fd_source);
        return -1;
    }

    char buffer[READ_WRITE_CHUNK];
    int  byte_count;

    while ((byte_count = read(fd_source, buffer, READ_WRITE_CHUNK)) > 0) {
        int written = 0;
        while (written < byte_count) {
            int n = write(fd_dest, buffer + written, byte_count - written);
            if (n < 0) {
                log_err("[copy_file] write to '%s' failed: %s",
                        destination, strerror(errno));
                close(fd_source);
                close(fd_dest);
                return -1;
            }
            written += n;
        }
    }

    if (byte_count < 0) {
        log_err("[copy_file] read from '%s' failed: %s",
                source, strerror(errno));
        close(fd_source);
        close(fd_dest);
        return -1;
    }

    close(fd_source);
    close(fd_dest);
    return 0;
}

int check_file_exists(const char *filepath) {

    struct stat st = {0};

    if (stat(filepath, &st) == -1) {
        return -1;
    }

    return 0;
}

static int manifest_count_list_tokens(const char *str) {

    if (str == NULL || str[0] == '\0') return 0;

    int count = 0;
    int in_token = 0;
    int i = 0;

    while (str[i] != '\0') {
        if (str[i] != ' ') {
            if (!in_token) { count++; in_token = 1; }
        } else {
            in_token = 0;
        }
        i++;
    }

    return count;
}

static int manifest_find_mapping_index(const char *name) {

    if (mapping_names == NULL) return -1;
    for (int m = 0; m < mapping_count; m++) {
        if (mapping_names[m] && !strcmp(name, mapping_names[m])) return m;
    }
    return -1;
}

static int find_by_name(const char *name, struct instance **out) {

    for (int i = 0; i < instance_count; i++) {
        if (!strcmp(name, instance_list[i].name)) {
            *out = &instance_list[i];
            return 0;
        }
    }
    *out = NULL;
    return -1;
}

static int manifest_resolve_pointers(char ***scratch_after_keys,
                                     char ***scratch_before_keys) {

    if (mapping_count == 0) return 0;

    for (int i = 0; i < instance_count; i++) {

        for (int m = 0; m < mapping_count; m++) {

            /* ----- after side ----- */
            const char *raw_a = (scratch_after_keys && scratch_after_keys[i])
                                ? scratch_after_keys[i][m] : NULL;
            if (raw_a == NULL || raw_a[0] == '\0') {
                instance_list[i].after_lengths[m] = 0;
                instance_list[i].after_list[m]    = NULL;
            } else {
                int n = manifest_count_list_tokens(raw_a);
                instance_list[i].after_lengths[m] = n;
                if (n == 0) {
                    instance_list[i].after_list[m] = NULL;
                } else {
                    instance_list[i].after_list[m] =
                        (struct instance **) calloc((size_t) n, sizeof(struct instance *));
                    char *tmp = strdup(raw_a);
                    char *tok = strtok(tmp, " ");
                    int j = 0;
                    while (tok != NULL && j < n) {
                        struct instance *found = NULL;
                        if (find_by_name(tok, &found) < 0) {
                            log_err("[resolve_pointers] '%s' in after of mapping '%s' of instance '%s' does not match any instance name",
                                    tok,
                                    mapping_names[m] ? mapping_names[m] : "(unnamed)",
                                    instance_list[i].name);
                            free(tmp);
                            return -1;
                        }
                        instance_list[i].after_list[m][j++] = found;
                        tok = strtok(NULL, " ");
                    }
                    free(tmp);
                }
            }

            /* ----- before side ----- */
            const char *raw_b = (scratch_before_keys && scratch_before_keys[i])
                                ? scratch_before_keys[i][m] : NULL;
            if (raw_b == NULL || raw_b[0] == '\0') {
                instance_list[i].before_lengths[m] = 0;
                instance_list[i].before_list[m]    = NULL;
            } else {
                int n = manifest_count_list_tokens(raw_b);
                instance_list[i].before_lengths[m] = n;
                if (n == 0) {
                    instance_list[i].before_list[m] = NULL;
                } else {
                    instance_list[i].before_list[m] =
                        (struct instance **) calloc((size_t) n, sizeof(struct instance *));
                    char *tmp = strdup(raw_b);
                    char *tok = strtok(tmp, " ");
                    int j = 0;
                    while (tok != NULL && j < n) {
                        struct instance *found = NULL;
                        if (find_by_name(tok, &found) < 0) {
                            log_err("[resolve_pointers] '%s' in before of mapping '%s' of instance '%s' does not match any instance name",
                                    tok,
                                    mapping_names[m] ? mapping_names[m] : "(unnamed)",
                                    instance_list[i].name);
                            free(tmp);
                            return -1;
                        }
                        instance_list[i].before_list[m][j++] = found;
                        tok = strtok(NULL, " ");
                    }
                    free(tmp);
                }
            }
        }
    }

    return 0;
}

const char *failure_behavior_to_string(enum failure_behavior fb) {
    switch (fb) {
        case FAILURE_BEHAVIOR_ABORT:   return "Abort";
        case FAILURE_BEHAVIOR_RESTART: return "Restart";
    }
    return "(unknown)";
}

const char *mapping_behavior_to_string(enum mapping_behavior mb) {
    switch (mb) {
        case MAPPING_BEHAVIOR_IGNORE:  return "Ignore";
        case MAPPING_BEHAVIOR_CASCADE: return "Cascade";
    }
    return "(unknown)";
}
