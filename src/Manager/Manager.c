#include "Unit_Starter.h"
#include "Constant_Limits.h"

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

/* ---------------------------------------------------------------------------
 * Instance type — distinguishes service-backed entries from target-backed
 * groups.
 * --------------------------------------------------------------------------- */

enum instance_type {
    INSTANCE_ENTRY,   /* Runnable program. Maps to a .service unit file. */
    INSTANCE_GROUP    /* Organisational container. Maps to a .target unit file. */
};

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
 * FailureBehavior buffer size. Large enough for the longest value
 * ("Restart") plus a null terminator, with slack.
 * --------------------------------------------------------------------------- */

#define LIMIT_FAILURE_BEHAVIOR 16

/* ---------------------------------------------------------------------------
 * Instance — unified runtime representation of one Entry or Group.
 *
 * Scalar fields are fixed-size buffers. List fields are pointer arrays
 * into instance_list, populated by a second resolver pass after all
 * instances are parsed from the manifest.
 * --------------------------------------------------------------------------- */

struct instance {
    char key[LIMIT_ENTRY_NAME];
    char name[LIMIT_ENTRY_NAME];
    char unit_file_name[LIMIT_UNIT_NAME];
    enum instance_type type;
    char path[PATH_MAX];
    char command[LIMIT_FILE_BUFFER];
    int  order;
    char failure_behavior[LIMIT_FAILURE_BEHAVIOR];

    struct instance **after;
    uint32_t after_count;
    struct instance **before;
    uint32_t before_count;
    struct instance **group;
    uint32_t group_count;
    struct instance **members;
    uint32_t members_count;

    /* Raw key strings held until pointer resolution pass. */
    char *after_keys;
    char *before_keys;
    char *group_keys;
    char *members_keys;

    /* Pointer to the tracked_unit owned by Unit_Starter. Set after
     * tracked_units_register + attach. All runtime state (active_state,
     * sub_state, load_state, exec_main_pid, etc.) is read from this
     * struct — Manager does not duplicate it. */
    tracked_unit *tracked;
};

/* ---------------------------------------------------------------------------
 * Global instance list
 * --------------------------------------------------------------------------- */

static struct instance *instance_list = NULL;
static int instance_count = 0;
static int running_entries = 0;

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
static int   operation_mode              = 0;
static char *failure_behavior_default    = NULL;
static char *dependency_behavior_default = NULL;

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

/* ---------------------------------------------------------------------------
 * Forward declarations
 * --------------------------------------------------------------------------- */

/* Manager */
int manager_init();
int manager_tear_down();
int manager_read_config(const char *filename);
int manager_read_input();
int manager_read_manifest(const char *filename);
int manager_register_tracked_units();
int manager_capture_instances();
int manager_start_instances();
int manager_terminate_instances();
int manager_copy_unit_files();
int manager_purge_instances();
int manager_operate_instance(struct instance *ins, enum instance_operation op);
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
int path_resolver(char *abspath, const char *path);
int build_path(char *out, ...);
int create_results_folder();
int create_unit_file_dump_dir();
int create_log_dir();
int create_runtime_dirs();
int copy_file(const char *source, const char *destination);
int check_file_exists(const char *filepath);
struct instance *find_instance(const char *instance_name, int n);
static int manifest_count_list_tokens(const char *str);
static int manifest_resolve_pointers();
int find_by_key(const char *key, struct instance **out);
int find_by_name(const char *name, struct instance **out);
int find_by_type(enum instance_type type, struct instance **out, int *out_count);

/* ---------------------------------------------------------------------------
 * Step runner macros.
 *
 * RUN_STEP wraps a forward-progress step (1–7): banner in, run, banner out;
 * a negative return aborts the pipeline by jumping to the `cleanup:` label.
 *
 * RUN_CLEANUP_STEP wraps a cleanup step (8–9): still logs the banner and
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

    RUN_STEP(1, manager_init,                   manager_init());
    RUN_STEP(2, manager_read_config,            manager_read_config("./src/Launcher.config"));
    RUN_STEP(3, create_runtime_dirs,            create_runtime_dirs());

    /* Master log opens now that log_directory exists. Failure is non-fatal —
     * the logger transparently falls back to stdout/stderr only. */
    logger_init(log_directory);

    RUN_STEP(4, manager_read_input,             manager_read_input());
    debug_print_instances(-1, "01_read_input.log");

    RUN_STEP(5, manager_copy_unit_files,        manager_copy_unit_files());
    RUN_STEP(6, manager_register_tracked_units, manager_register_tracked_units());
    RUN_STEP(7, manager_capture_instances,      manager_capture_instances());
    debug_print_instances(-1, "02_capture.log");

cleanup:
    RUN_CLEANUP_STEP(8, manager_purge_instances, manager_purge_instances());
    RUN_CLEANUP_STEP(9, manager_tear_down,       manager_tear_down());

    return 0;
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
    dependency_behavior_default = (char *) calloc(LIMIT_FAILURE_BEHAVIOR, sizeof(char));

    int rc;

    log_info("[init] connecting to user bus");
    rc = dbus_init();
    if (rc < 0) { log_err("[init] dbus_init rc=%d", rc); return rc; }

    log_info("[init] subscribing to systemd");
    rc = dbus_subscribe_systemd();
    if (rc < 0) { log_err("[init] subscribe rc=%d", rc); return rc; }

    log_info("[init] arming Reloading listener");
    rc = dbus_daemon_reload_listener();
    if (rc < 0) { log_err("[init] Reloading listener rc=%d", rc); return rc; }

    log_info("[init] arming UnitNew listener");
    rc = dbus_unit_new_listener();
    if (rc < 0) { log_err("[init] UnitNew listener rc=%d", rc); return rc; }

    log_info("[init] arming UnitRemoved listener");
    rc = dbus_unit_removed_listener();
    if (rc < 0) { log_err("[init] UnitRemoved listener rc=%d", rc); return rc; }

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
    free(dependency_behavior_default);

    /* Free per-instance heap allocations. */
    if (instance_list != NULL) {
        int i;
        for (i = 0; i < instance_count; i++) {
            free(instance_list[i].after);
            free(instance_list[i].before);
            free(instance_list[i].group);
            free(instance_list[i].members);
            free(instance_list[i].after_keys);
            free(instance_list[i].before_keys);
            free(instance_list[i].group_keys);
            free(instance_list[i].members_keys);
        }
        free(instance_list);
    }

    log_info("[tear_down] freed globals and instance list");

    logger_tear_down();

    return rc;
}

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
        int n = fread(&contents[cursor], sizeof(char), READ_WRITE_CHUNK, f);
        if (!n) break;
        cursor += n;
    }
    fclose(f);

    contents[LIMIT_FILE_BUFFER - 1] = '\0';

    char **path_to_fill                   = NULL;
    int    is_operation_mode              = 0;
    int    is_failure_behavior_default    = 0;
    int    is_dependency_behavior_default = 0;
    int    start = 0, end = 0, mode = 0;

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
            is_operation_mode              = 0;
            is_failure_behavior_default    = 0;
            is_dependency_behavior_default = 0;

            if      (!strncmp("RESULTS_FOLDER",              &contents[start], dist)) path_to_fill                   = &results_folder;
            else if (!strncmp("UNIT_FILE_STAGING_DIRECTORY", &contents[start], dist)) path_to_fill                = &unit_file_staging_directory;
            else if (!strncmp("UNIT_FILE_DESTINATION",       &contents[start], dist)) path_to_fill                = &unit_file_destination;
            else if (!strncmp("VENV_DIRECTORY",              &contents[start], dist)) path_to_fill                = &venv_directory;
            else if (!strncmp("INPUT_FILE",                  &contents[start], dist)) path_to_fill                = &input_file_path;
            else if (!strncmp("LAUNCHER_SCRIPT",             &contents[start], dist)) path_to_fill                = &launcher_script_path;
            else if (!strncmp("MANIFEST_FILE",               &contents[start], dist)) path_to_fill                = &manifest_file_path;
            else if (!strncmp("LOG_DIRECTORY",               &contents[start], dist)) path_to_fill                = &log_directory;
            else if (!strncmp("OPERATION_MODE",              &contents[start], dist)) is_operation_mode           = 1;
            else if (!strncmp("FailureBehaviorDefault",      &contents[start], dist)) is_failure_behavior_default    = 1;
            else if (!strncmp("DependencyBehaviorDefault",   &contents[start], dist)) is_dependency_behavior_default = 1;

            mode = 1;
            end++;
            start = end;

        } else if (contents[end] == '\n' && mode == 1) {

            unsigned long dist = end - start;
            char raw[PATH_MAX] = {0};
            strncpy(raw, &contents[start], dist);

            if (path_to_fill != NULL) {
                path_resolver(*path_to_fill, raw);
            } else if (is_operation_mode) {
                operation_mode = (!strncmp("daemon", raw, dist)) ? 1 : 0;
            } else if (is_failure_behavior_default) {
                strncpy(failure_behavior_default, raw, LIMIT_FAILURE_BEHAVIOR - 1);
                failure_behavior_default[LIMIT_FAILURE_BEHAVIOR - 1] = '\0';
            } else if (is_dependency_behavior_default) {
                strncpy(dependency_behavior_default, raw, LIMIT_FAILURE_BEHAVIOR - 1);
                dependency_behavior_default[LIMIT_FAILURE_BEHAVIOR - 1] = '\0';
            }

            path_to_fill                   = NULL;
            is_operation_mode              = 0;
            is_failure_behavior_default    = 0;
            is_dependency_behavior_default = 0;
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
        strncpy(failure_behavior_default, "Ignore", LIMIT_FAILURE_BEHAVIOR - 1);
    }
    if (!dependency_behavior_default[0]) {
        strncpy(dependency_behavior_default, "Ignore", LIMIT_FAILURE_BEHAVIOR - 1);
    }

    /* Per-action echo of every parsed value. */
    log_info("[read_config] RESULTS_FOLDER              = %s", results_folder);
    log_info("[read_config] UNIT_FILE_STAGING_DIRECTORY = %s", unit_file_staging_directory);
    log_info("[read_config] UNIT_FILE_DESTINATION       = %s", unit_file_destination);
    log_info("[read_config] VENV_DIRECTORY              = %s", venv_directory);
    log_info("[read_config] INPUT_FILE                  = %s", input_file_path);
    log_info("[read_config] LAUNCHER_SCRIPT             = %s", launcher_script_path);
    log_info("[read_config] MANIFEST_FILE               = %s", manifest_file_path);
    log_info("[read_config] LOG_DIRECTORY               = %s", log_directory);
    log_info("[read_config] OPERATION_MODE              = %s", operation_mode ? "daemon" : "basic");
    log_info("[read_config] FailureBehaviorDefault      = %s", failure_behavior_default);
    log_info("[read_config] DependencyBehaviorDefault   = %s", dependency_behavior_default);

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

    /* Pre-existing inputs: venv, input YAML, launcher script must be
     * accessible before the launcher subprocess runs in step 5. */
    if (access(venv_directory,       F_OK) != 0) { log_err("[read_config] verify: VENV_DIRECTORY not accessible: %s (%s)", venv_directory,       strerror(errno)); return -1; }
    if (access(input_file_path,      F_OK) != 0) { log_err("[read_config] verify: INPUT_FILE not accessible: %s (%s)",     input_file_path,      strerror(errno)); return -1; }
    if (access(launcher_script_path, F_OK) != 0) { log_err("[read_config] verify: LAUNCHER_SCRIPT not accessible: %s (%s)", launcher_script_path, strerror(errno)); return -1; }

    log_info("[read_config] verify: 8/8 required paths non-empty; pre-existing inputs accessible");

    return 0;
}

int manager_read_input() {

    log_info("[read_input] forking launcher");
    pid_t child = fork();

    if (child > 0) {

        int status;
        if (waitpid(child, &status, 0) < 0) {
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

            if (!ins->key[0] || !ins->name[0] || !ins->unit_file_name[0]) {
                log_err("[read_input] verify: instance %d has empty identity "
                        "field (key='%s' name='%s' unit='%s')",
                        i, ins->key, ins->name, ins->unit_file_name);
                return -1;
            }
            for (uint32_t j = 0; j < ins->after_count;   j++) {
                if (!ins->after[j])   {
                    unresolved++;
                    log_err("[read_input] verify: %s after[%u] unresolved",   ins->key, j);
                }
            }
            for (uint32_t j = 0; j < ins->before_count;  j++) {
                if (!ins->before[j])  {
                    unresolved++;
                    log_err("[read_input] verify: %s before[%u] unresolved",  ins->key, j);
                }
            }
            for (uint32_t j = 0; j < ins->group_count;   j++) {
                if (!ins->group[j])   {
                    unresolved++;
                    log_err("[read_input] verify: %s group[%u] unresolved",   ins->key, j);
                }
            }
            for (uint32_t j = 0; j < ins->members_count; j++) {
                if (!ins->members[j]) {
                    unresolved++;
                    log_err("[read_input] verify: %s members[%u] unresolved", ins->key, j);
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

        char *launcher_cmd       = (char *) calloc(1 << 12, sizeof(char));
        char  activate[PATH_MAX] = {0};
        build_path(activate, venv_directory, "bin/activate", (char *) NULL);
        snprintf(launcher_cmd, 1 << 12,
                 "source %s && PYTHONPATH=./src python3 %s "
                 "INPUT_FILE=%s "
                 "UNIT_FILE_OUTPUT_PATH=%s "
                 "MANIFEST_FILE_PATH=%s "
                 "FAILURE_BEHAVIOR_DEFAULT=%s "
                 "DEPENDENCY_BEHAVIOR_DEFAULT=%s "
                 "&& deactivate",
                 activate,
                 launcher_script_path,
                 input_file_path,
                 unit_file_staging_directory,
                 manifest_file_path,
                 failure_behavior_default,
                 dependency_behavior_default
        );
        execl("/bin/bash", "bash", "-c", launcher_cmd, (char *) NULL);
        /* Only reached on execl failure. */
        free(launcher_cmd);
        _exit(127);

    } else {
        log_err("[read_input] fork failed: %s", strerror(errno));
        return -1;
    }
}

int manager_read_manifest(const char *filename) {

    char *contents = (char *) calloc(LIMIT_FILE_BUFFER, sizeof(char));

    FILE *f = fopen(filename, "r");
    if (f == NULL) {
        log_err("[read_manifest] cannot open manifest: %s (%s)",
                filename, strerror(errno));
        free(contents);
        return -1;
    }

    int cursor = 0;
    while (1) {
        int n = fread(&contents[cursor], sizeof(char), READ_WRITE_CHUNK, f);
        if (!n) break;
        cursor += n;
    }
    fclose(f);

    contents[LIMIT_FILE_BUFFER - 1] = '\0';

    /* Count INSTANCE blocks to allocate instance_list. */
    int count = 0;
    char *scan = contents;
    while ((scan = strstr(scan, "INSTANCE\n")) != NULL) {
        count++;
        scan++;
    }

    instance_count = count;
    instance_list  = (struct instance *) calloc(instance_count, sizeof(struct instance));

    int idx = 0;
    int start = 0, end = 0;
    int in_block = 0;

    while (contents[end] != '\0') {

        if (contents[end] == '\n') {

            int len = end - start;

            if (len <= 0) {
                end++;
                start = end;
                continue;
            }

            char line[LIMIT_FILE_BUFFER] = {0};
            strncpy(line, &contents[start], len);

            if (!strncmp("INSTANCE", line, 8)) {
                in_block = 1;

            } else if (!strncmp("END", line, 3) && in_block) {
                in_block = 0;
                idx++;

            } else if (in_block) {

                char *eq = strchr(line, '=');
                if (eq == NULL) {
                    end++;
                    start = end;
                    continue;
                }

                *eq = '\0';
                char *key_str   = line;
                char *value_str = eq + 1;
                int   vlen      = len - (int)(eq - line) - 1;

                struct instance *ins = &instance_list[idx];

                if      (!strcmp(key_str, "key"))            strncpy(ins->key,            value_str, LIMIT_ENTRY_NAME  - 1);
                else if (!strcmp(key_str, "name"))           strncpy(ins->name,           value_str, LIMIT_ENTRY_NAME  - 1);
                else if (!strcmp(key_str, "unit_file_name")) strncpy(ins->unit_file_name, value_str, LIMIT_UNIT_NAME   - 1);
                else if (!strcmp(key_str, "path"))           strncpy(ins->path,           value_str, PATH_MAX          - 1);
                else if (!strcmp(key_str, "command"))        strncpy(ins->command,        value_str, LIMIT_FILE_BUFFER - 1);
                else if (!strcmp(key_str, "type")) {
                    if      (!strncmp(value_str, "ENTRY", 5)) ins->type = INSTANCE_ENTRY;
                    else if (!strncmp(value_str, "GROUP", 5)) ins->type = INSTANCE_GROUP;
                }
                else if (!strcmp(key_str, "after"))            { ins->after_keys   = (char *) calloc(vlen + 1, sizeof(char)); strncpy(ins->after_keys,   value_str, vlen); }
                else if (!strcmp(key_str, "before"))           { ins->before_keys  = (char *) calloc(vlen + 1, sizeof(char)); strncpy(ins->before_keys,  value_str, vlen); }
                else if (!strcmp(key_str, "group"))            { ins->group_keys   = (char *) calloc(vlen + 1, sizeof(char)); strncpy(ins->group_keys,   value_str, vlen); }
                else if (!strcmp(key_str, "members"))          { ins->members_keys = (char *) calloc(vlen + 1, sizeof(char)); strncpy(ins->members_keys, value_str, vlen); }
                else if (!strcmp(key_str, "order"))            { ins->order = atoi(value_str); }
                else if (!strcmp(key_str, "failure_behavior")) { strncpy(ins->failure_behavior, value_str, LIMIT_FAILURE_BEHAVIOR - 1); ins->failure_behavior[LIMIT_FAILURE_BEHAVIOR - 1] = '\0'; }
            }

            end++;
            start = end;

        } else {
            end++;
        }
    }

    free(contents);

    /* Any instance whose manifest block did not carry a failure_behavior=
     * line (older manifests, or mid-transition regressions) falls back to
     * the config default so the field is never empty downstream. */
    for (int i = 0; i < instance_count; i++) {
        if (!instance_list[i].failure_behavior[0]) {
            strncpy(instance_list[i].failure_behavior,
                    failure_behavior_default[0] ? failure_behavior_default : "Ignore",
                    LIMIT_FAILURE_BEHAVIOR - 1);
        }
    }

    return manifest_resolve_pointers();
}

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

int manager_start_instances() {

    int i, j;

    /* Build a list of entry instances sorted by their start order.
     * Entries with lower order values are started first. */
    int entry_count = 0;
    struct instance **entries = (struct instance **) calloc(instance_count, sizeof(struct instance *));

    for (i = 0; i < instance_count; i++) {
        if (instance_list[i].type == INSTANCE_ENTRY) {
            entries[entry_count++] = &instance_list[i];
        }
    }

    /* Insertion sort by order — stable and sufficient for small N. */
    for (i = 1; i < entry_count; i++) {
        struct instance *tmp = entries[i];
        j = i - 1;
        while (j >= 0 && entries[j]->order > tmp->order) {
            entries[j + 1] = entries[j];
            j--;
        }
        entries[j + 1] = tmp;
    }

    for (i = 0; i < entry_count; i++) {
        running_entries++;
        manager_operate_instance(entries[i], INSTANCE_OP_START);
    }

    free(entries);

    return 0;
}

int manager_terminate_instances() {

    int i;

    for (i = 0; i < instance_count; i++) {
        if (instance_list[i].type == INSTANCE_ENTRY) {
            manager_operate_instance(&instance_list[i], INSTANCE_OP_STOP);
        }
    }

    return 0;
}

int manager_purge_instances() {

    int first_err = 0;
    int i;
    char filepath[PATH_MAX] = {0};

    /* Disable all instances — best-effort. */
    for (i = 0; i < instance_count; i++) {
        int rc = manager_operate_instance(&instance_list[i], INSTANCE_OP_DISABLE);
        if (rc < 0) {
            log_err("[purge] disable %s rc=%d", instance_list[i].unit_file_name, rc);
            if (!first_err) first_err = rc;
        } else {
            log_info("[purge] disable %s -> OK", instance_list[i].unit_file_name);
        }
    }

    /* Remove unit files from the systemd user directory. */
    for (i = 0; i < instance_count; i++) {
        build_path(filepath, unit_file_destination,
                   instance_list[i].unit_file_name, (char *) NULL);
        if (unlink(filepath) != 0) {
            if (errno == ENOENT) {
                log_info("[purge] unlink %s -> already gone", filepath);
            } else {
                log_err("[purge] unlink %s failed: %s", filepath, strerror(errno));
                if (!first_err) first_err = -1;
            }
        } else {
            log_info("[purge] unlink %s -> OK", filepath);
        }
    }

    /* Reload so systemd forgets the removed unit files. */
    int rc = dbus_daemon_reload_sender();
    if (rc < 0) {
        log_err("[purge] reload rc=%d", rc);
        if (!first_err) first_err = rc;
    } else {
        log_info("[purge] daemon reloaded");
    }

    return first_err;
}

/* ---------------------------------------------------------------------------
 * manager_operate_instance — core operation primitive.
 *
 * Executes the requested operation on the given instance via the sd-bus
 * wrapper functions in Unit_Starter.c. Reads results back into the instance
 * struct where applicable.
 * --------------------------------------------------------------------------- */

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

/* ---------------------------------------------------------------------------
 * on_unit_state_changed — called by Unit_Starter.c whenever a tracked
 * property is updated via a PropertiesChanged signal or initial snapshot.
 * --------------------------------------------------------------------------- */

static void on_unit_state_changed(const tracked_unit *u,
                                  const char *property_name,
                                  void *userdata) {

    (void) userdata;

    if (!u) return;

    if (strcmp(property_name, "ActiveState") != 0 &&
        strcmp(property_name, "(initial)")   != 0) {
        return;
    }

    log_info("[StateChanged] unit=%-30s active=%-12s sub=%s",
             u->name,
             active_state_to_string(u->active_state),
             u->sub_state);

    /* Find the instance that owns this tracked_unit. */
    struct instance *ins = NULL;
    for (int i = 0; i < instance_count; i++) {
        if (instance_list[i].tracked == u) {
            ins = &instance_list[i];
            break;
        }
    }
    if (!ins) return;

    /* A started entry reaching inactive or failed is a termination. */
    if (ins->type == INSTANCE_ENTRY &&
        (u->active_state == ACTIVE_STATE_INACTIVE ||
         u->active_state == ACTIVE_STATE_FAILED) &&
        running_entries > 0) {
        running_entries--;
        log_info("[Terminated]   unit=%-30s running_entries=%d",
                 u->name, running_entries);
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
    log_info("%s", "");
    log_info("========================================");
    log_info(" [%2d/%d] %s", idx, STEP_TOTAL, step_name);
    log_info("========================================");
}

void step_end(int idx, const char *step_name, int rc) {
    (void) idx;
    struct timespec end_time;
    clock_gettime(CLOCK_MONOTONIC, &end_time);
    long elapsed_ms = (end_time.tv_sec  - step_start_time.tv_sec)  * 1000L
                    + (end_time.tv_nsec - step_start_time.tv_nsec) / 1000000L;
    if (rc < 0) {
        log_err("[%s] FAIL (rc=%d, %ld ms)", step_name, rc, elapsed_ms);
    } else {
        log_info("[%s] OK (%ld ms)", step_name, elapsed_ms);
    }
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

        dprintf(out, "\n  [%s]\n", ins->key);

        /* --- Scalar identity fields --- */
        dprintf(out, "    name              : %s\n", ins->name);
        dprintf(out, "    unit_file_name    : %s\n", ins->unit_file_name);
        dprintf(out, "    type              : %s\n",
                ins->type == INSTANCE_ENTRY ? "ENTRY" : "GROUP");
        dprintf(out, "    path              : %s\n", ins->path);
        dprintf(out, "    command           : %s\n", ins->command);
        dprintf(out, "    order             : %d\n", ins->order);
        dprintf(out, "    failure_behavior  : %s\n",
                ins->failure_behavior[0] ? ins->failure_behavior : "(none)");

        /* --- Relationship lists --- */
        dprintf(out, "    after             :");
        if (ins->after_count == 0) {
            dprintf(out, " (none)");
        } else {
            for (j = 0; j < (int) ins->after_count; j++)
                dprintf(out, " %s", ins->after[j] ? ins->after[j]->key : "(unresolved)");
        }
        dprintf(out, "\n");

        dprintf(out, "    before            :");
        if (ins->before_count == 0) {
            dprintf(out, " (none)");
        } else {
            for (j = 0; j < (int) ins->before_count; j++)
                dprintf(out, " %s", ins->before[j] ? ins->before[j]->key : "(unresolved)");
        }
        dprintf(out, "\n");

        dprintf(out, "    group             :");
        if (ins->group_count == 0) {
            dprintf(out, " (none)");
        } else {
            for (j = 0; j < (int) ins->group_count; j++)
                dprintf(out, " %s", ins->group[j] ? ins->group[j]->key : "(unresolved)");
        }
        dprintf(out, "\n");

        dprintf(out, "    members           :");
        if (ins->members_count == 0) {
            dprintf(out, " (none)");
        } else {
            for (j = 0; j < (int) ins->members_count; j++)
                dprintf(out, " %s", ins->members[j] ? ins->members[j]->key : "(unresolved)");
        }
        dprintf(out, "\n");

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
            if (ins->type == INSTANCE_ENTRY) {
                dprintf(out, "    exec_main_pid     : %u\n", t->exec_main_pid);
                dprintf(out, "    exec_main_status  : %d\n", t->exec_main_status);
                dprintf(out, "    result            : %s\n",
                        t->result[0] ? t->result : "(unknown)");
            }
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

/* mkdir_p — create a directory and all missing parents (mode 0755).
 * Returns 0 on success or if the leaf already exists, -1 on error. */
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

struct instance *find_instance(const char *instance_name, int n) {

    int i;
    for (i = 0; i < instance_count; i++) {
        if (!strncmp(instance_name, instance_list[i].name, n)) {
            return &instance_list[i];
        }
    }
    return NULL;
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

static int manifest_resolve_pointers() {

    int i;
    for (i = 0; i < instance_count; i++) {

        struct instance *ins = &instance_list[i];

        #define RESOLVE_LIST(raw_keys, ptr_array, cnt_field)                              \
        do {                                                                               \
            int n = manifest_count_list_tokens(raw_keys);                                  \
            ins->cnt_field = (uint32_t) n;                                                 \
            if (n > 0) {                                                                   \
                ins->ptr_array = (struct instance **) calloc(n, sizeof(struct instance *));\
                char *tmp = strdup(raw_keys);                                              \
                char *tok = strtok(tmp, " ");                                              \
                int j = 0;                                                                 \
                while (tok != NULL && j < n) {                                            \
                    struct instance *found = NULL;                                         \
                    find_by_key(tok, &found);                                              \
                    ins->ptr_array[j++] = found;                                           \
                    tok = strtok(NULL, " ");                                               \
                }                                                                          \
                free(tmp);                                                                 \
            }                                                                              \
        } while (0)

        RESOLVE_LIST(ins->after_keys,   after,   after_count);
        RESOLVE_LIST(ins->before_keys,  before,  before_count);
        RESOLVE_LIST(ins->group_keys,   group,   group_count);
        RESOLVE_LIST(ins->members_keys, members, members_count);

        #undef RESOLVE_LIST
    }

    return 0;
}

int find_by_key(const char *key, struct instance **out) {

    int i;
    for (i = 0; i < instance_count; i++) {
        if (!strcmp(key, instance_list[i].key)) {
            *out = &instance_list[i];
            return 0;
        }
    }
    *out = NULL;
    return -1;
}

int find_by_name(const char *name, struct instance **out) {

    int i;
    for (i = 0; i < instance_count; i++) {
        if (!strcmp(name, instance_list[i].name)) {
            *out = &instance_list[i];
            return 0;
        }
    }
    *out = NULL;
    return -1;
}

int find_by_type(enum instance_type type, struct instance **out, int *out_count) {

    int i, n = 0;
    for (i = 0; i < instance_count; i++) {
        if (instance_list[i].type == type) {
            out[n++] = &instance_list[i];
        }
    }
    *out_count = n;
    return 0;
}
