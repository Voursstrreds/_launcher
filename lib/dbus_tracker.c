#include "dbus_tracker.h"
#include <stdarg.h>
#include <string.h>
#include <errno.h>

sd_bus *bus = NULL;
sd_bus_slot *slot = NULL;
sd_bus_message *message = NULL;
sd_bus_error error = SD_BUS_ERROR_NULL;

sd_bus_slot *slot_unit_new = NULL;
sd_bus_slot *slot_unit_removed = NULL;
sd_bus_slot *slot_property_changed = NULL;
sd_bus_slot *slot_reloading = NULL;

const char *name_register = "launch_manager.systemdreader.LaunchManager";
const char *path_register = "/launch_manager/systemdreader/LaunchManager";
const char *interface_name_register = "launch_manager.systemdreader.LaunchManager.UnitManager";
const char *interface_path_register = "/launch_manager/systemdreader/LaunchManager/UnitManager";

const char *destination_systemd = "org.freedesktop.systemd1";
const char *path_systemd = "/org/freedesktop/systemd1";

const char *interface_manager_systemd = "org.freedesktop.systemd1.Manager";
const char *interface_service_systemd = "org.freedesktop.systemd1.Service";
const char *interface_job_systemd = "org.freedesktop.systemd1.Job";
const char *interface_unit_systemd = "org.freedesktop.systemd1.Unit";

const char *signal_reload = "Reloading";
const char *signal_unit_new = "UnitNew";
const char *signal_unit_removed = "UnitRemoved";
const char *signal_property_changed = "PropertyChanged";

const char *member_subscribe_systemd = "Subscribe";
const char *member_unsubscribe_systemd = "Unsubscribe";
const char *member_load_unit_systemd = "LoadUnit";
const char *member_start_unit_systemd = "StartUnit";
const char *member_stop_unit_systemd = "StopUnit";
const char *member_reload_unit_systemd = "ReloadUnit";
const char *member_restart_unit_systemd = "RestartUnit";
const char *member_reload_systemd = "Reload";
const char *member_start_transient_unit_systemd;
const char *member_service_property_execstart_systemd = "ExecStart";
const char *member_get_unit_systemd = "GetUnit";
const char *member_enable_unit_file_systemd = "EnableUnitFiles";
const char *member_disable_unit_file_systemd = "DisableUnitFiles";
const char *member_reset_failed_unit_systemd = "ResetFailedUnit";
const char *member_reset_failed_systemd = "ResetFailed";

const char *interface_properties_dbus = "org.freedesktop.DBus.Properties";
const char *signal_properties_changed_dbus = "PropertiesChanged";
const char *member_get_dbus = "Get";

const char *property_active_state_systemd       = "ActiveState";
const char *property_sub_state_systemd          = "SubState";
const char *property_load_state_systemd         = "LoadState";
const char *property_unit_file_state_systemd    = "UnitFileState";
const char *property_exec_main_pid_systemd      = "ExecMainPID";
const char *property_exec_main_status_systemd   = "ExecMainStatus";
const char *property_result_systemd             = "Result";
const char *property_timeout_start_usec_systemd = "TimeoutStartUSec";
const char *property_start_limit_burst_systemd  = "StartLimitBurst";

tracked_unit tracked_units[LIMIT_TRACKED_UNITS];
size_t tracked_units_count = 0;

// State-changed callback registered by the program. NULL until the program
// opts in via tracked_units_set_state_changed_cb. Fired from the initial
// snapshot inside tracked_unit_attach and from dbus_properties_changed_handler.
static tracked_unit_state_changed_cb state_changed_cb = NULL;
static void *state_changed_ud = NULL;

void tracked_units_set_state_changed_cb(tracked_unit_state_changed_cb cb, void *userdata) {
    state_changed_cb = cb;
    state_changed_ud = userdata;
}

const sd_bus_vtable vtable[] = {
    SD_BUS_VTABLE_START(0),
    SD_BUS_VTABLE_END
};


// Release the shared message and error state before reusing them
// in the next sd-bus call. Safe to call when they are already clean.
// Must be called at the top of any wrapper that passes &message or &error
// to an sd-bus function.
void dbus_reset_io(void) {
    if (message) {
        sd_bus_message_unref(message);
        message = NULL;
    }
    sd_bus_error_free(&error);
}


int dbus_init() {

    int retval;

    // Registering to the user bus
    retval = sd_bus_default_user(&bus);
    DBUS_ERROR_READ;
    if (retval < 0) return retval;

    // Register the name.
    retval = sd_bus_request_name(bus, name_register, SD_BUS_NAME_ALLOW_REPLACEMENT | SD_BUS_NAME_REPLACE_EXISTING);
    DBUS_ERROR_READ;
    if (retval < 0) return retval;

    // VTABLE, Not adding a vtable causes busctl introspect incorrect.
    // There must be at least one interface to use busctl features.
    retval = sd_bus_add_object_vtable(bus,
                                      &slot,
                                      interface_path_register,
                                      interface_name_register,
                                      vtable,
                                      NULL);
    DBUS_ERROR_READ;
    if (retval < 0) return retval;

    return 0;
}

int dbus_tear_down() {

    int retval;

    retval = sd_bus_release_name(bus, name_register);
    DBUS_ERROR_READ;
    // Exception to the "return retval after DBUS_ERROR_READ" rule:
    // teardown must run all cleanup steps even if releasing the name fails,
    // otherwise slots and the bus would leak. The release failure is
    // remembered in retval and returned at the end.

    // Unref any slots we own.
    slot                  = sd_bus_slot_unref(slot);
    slot_unit_new         = sd_bus_slot_unref(slot_unit_new);
    slot_unit_removed     = sd_bus_slot_unref(slot_unit_removed);
    slot_property_changed = sd_bus_slot_unref(slot_property_changed);
    slot_reloading        = sd_bus_slot_unref(slot_reloading);

    // Drop any lingering message/error state.
    dbus_reset_io();

    sd_bus_close(bus);
    bus = sd_bus_unref(bus);

    return retval;
}

int dbus_subscribe_systemd() {

    dbus_reset_io();

    // Tell systemd to emit per-client signals to us.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_subscribe_systemd,
                                    &error,
                                    &message,
                                    ""
    );

    DBUS_ERROR_READ;

    return retval;

}

int dbus_unsubscribe_systemd() {

    dbus_reset_io();

    // Tell systemd to stop emitting per-client signals to us.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_unsubscribe_systemd,
                                    &error,
                                    &message,
                                    ""
    );

    DBUS_ERROR_READ;

    return retval;


}


int dbus_daemon_reload_listener() {

    int retval = sd_bus_match_signal(bus,
                                     &slot_reloading,
                                     destination_systemd,
                                     path_systemd,
                                     interface_manager_systemd,
                                     signal_reload,
                                     dbus_signal_handler,
                                     (void *)(uintptr_t) RELOADING
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_unit_new_listener() {

    int retval = sd_bus_match_signal(bus,
                                     &slot_unit_new,
                                     destination_systemd,
                                     path_systemd,
                                     interface_manager_systemd,
                                     signal_unit_new,
                                     dbus_signal_handler,
                                     (void *)(uintptr_t) UNIT_NEW
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_unit_removed_listener() {

    int retval = sd_bus_match_signal(bus,
                                     &slot_unit_removed,
                                     destination_systemd,
                                     path_systemd,
                                     interface_manager_systemd,
                                     signal_unit_removed,
                                     dbus_signal_handler,
                                     (void *)(uintptr_t) UNIT_REMOVED
    );

    DBUS_ERROR_READ;

    return retval;

}

int dbus_property_changed_listener(tracked_unit *u) {

    // Per-unit PropertiesChanged subscription. The signal is emitted from
    // the unit's own object path on the org.freedesktop.DBus.Properties
    // interface, NOT from the Manager path. Userdata is the tracked_unit
    // pointer so the handler can update the right cache entry directly.
    int retval = sd_bus_match_signal(bus,
                                     &u->slot_properties_changed,
                                     destination_systemd,
                                     u->object_path,
                                     interface_properties_dbus,
                                     signal_properties_changed_dbus,
                                     dbus_properties_changed_handler,
                                     u
    );

    DBUS_ERROR_READ;

    return retval;

}

int dbus_daemon_reload_sender() {

    dbus_reset_io();

    int retval = sd_bus_call_method(
        bus,
        destination_systemd,
        path_systemd,
        interface_manager_systemd,
        member_reload_systemd,
        &error,
        &message,
        ""
    );

    DBUS_ERROR_READ;

    return retval;
}


int dbus_start_unit(const char *unit_name, const char *mode) {

    dbus_reset_io();

    // Possible modes: replace, fail, isolate, ignore-dependencies, ignore-requirements.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_start_unit_systemd,
                                    &error,
                                    &message,
                                    "ss",
                                    unit_name,
                                    mode
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_stop_unit(const char *unit_name, const char *mode) {

    dbus_reset_io();

    // Possible modes: replace, fail, ignore-dependencies, ignore-requirements.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_stop_unit_systemd,
                                    &error,
                                    &message,
                                    "ss",
                                    unit_name,
                                    mode
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_load_unit(const char *unit_name) {

    dbus_reset_io();

    // Systemd load unit method call
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_load_unit_systemd,
                                    &error,
                                    &message,
                                    "s",
                                    unit_name
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_reload_unit(const char *unit_name) {

    return 0;
}

int dbus_restart_unit(const char *unit_name) {

    return 0;
}

int dbus_enable_unit(const char *unit_name) {

    dbus_reset_io();

    // Wrapper enables a single unit at a time; underlying method takes an
    // array of unit names, so we pass an array of length 1.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_enable_unit_file_systemd,
                                    &error,
                                    &message,
                                    "asbb",
                                    1,
                                    unit_name,
                                    0,
                                    0
    );

    DBUS_ERROR_READ;

    return retval;

}

int dbus_disable_unit(const char *unit_name) {

    dbus_reset_io();

    // Wrapper disables a single unit at a time; underlying method takes an
    // array of unit names, so we pass an array of length 1.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_disable_unit_file_systemd,
                                    &error,
                                    &message,
                                    "asb",
                                    1,
                                    unit_name,
                                    0
    );

    DBUS_ERROR_READ;

    return retval;

}

int dbus_reset_failed_unit(const char *unit_name) {

    dbus_reset_io();

    // ResetFailedUnit(s) on the Manager. Clears the failed state for the
    // named unit so a subsequent Start isn't blocked by a sticky failure.
    // Uses DBUS_ERROR_CLEAR because calling this on a unit that is not
    // currently loaded returns NoSuchUnit — a normal, expected condition
    // at startup that we don't want to print.
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_reset_failed_unit_systemd,
                                    &error,
                                    &message,
                                    "s",
                                    unit_name
    );

    DBUS_ERROR_CLEAR;

    return retval;
}

int dbus_reset_failed_all(void) {

    dbus_reset_io();

    // ResetFailed on the Manager with no arguments clears the failed state
    // of every unit systemd currently knows about. Kept as a convenience
    // alongside the per-unit form; neither is "the" correct choice, they
    // exist for different phases (per-unit during normal operation,
    // all-at-once for a nuclear reset).
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_reset_failed_systemd,
                                    &error,
                                    &message,
                                    ""
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_get_unit(const char *unit_name) {

    dbus_reset_io();

    int retval = sd_bus_call_method(
        bus,
        destination_systemd,
        path_systemd,
        interface_manager_systemd,
        member_get_unit_systemd,
        &error,
        &message,
        "s",
        unit_name
    );

    DBUS_ERROR_READ;

    return retval;
}

int dbus_get_property(const char *unit_object_path, const char *interface, const char *member, const char *signature) {

    dbus_reset_io();

    int retval = sd_bus_get_property(
        bus,
        destination_systemd,
        unit_object_path,
        interface,
        member,
        &error,
        &message,
        signature
    );

    DBUS_ERROR_READ;

    return retval;

}

int dbus_wait(uint64_t timeout_usec) {
    int retval = sd_bus_wait(bus, timeout_usec);

    DBUS_ERROR_READ;

    return retval;

}

int dbus_process(sd_bus_message **ret_message) {
    int retval = sd_bus_process(bus, ret_message);

    DBUS_ERROR_READ;

    return retval;

}




int dbus_signal_handler(sd_bus_message *m, void *userdata, sd_bus_error *ret_error) {

    enum signal_type signal = (enum signal_type)(uintptr_t) userdata;

    switch (signal) {
        case RELOADING:
        {
            int signal_value;
            sd_bus_message_read(m, "b", &signal_value); // 0 finished reload, 1 starting reload
            (void) signal_value;
        }
        return 0;

        case UNIT_NEW:
        {
            const char *id;
            const char *unit;
            sd_bus_message_read(m, "so", &id, &unit);

            tracked_unit *u = tracked_unit_find_by_name(id);
            if (u) {
                // Attach (or re-attach) the unit. If it was already attached,
                // detach first so we drop the stale PropertiesChanged slot
                // that referred to the previous object path.
                if (u->attached) {
                    tracked_unit_detach(u);
                }
                tracked_unit_attach(u);
            }
        }
        return 0;

        case UNIT_REMOVED:
        {
            const char *id;
            const char *unit;
            sd_bus_message_read(m, "so", &id, &unit);

            tracked_unit *u = tracked_unit_find_by_name(id);
            if (u) {
                tracked_unit_detach(u);
            }
        }
        return 0;

        case PROPERTY_CHANGED:
            break;
        default:
            return -1;
            break;
    }

    return 0;

}


// ---------------------------------------------------------------------------
// Tracked units: registration, lookup, attach/detach, property fetch,
// PropertiesChanged handler, enum parsers, state-change hook.
// ---------------------------------------------------------------------------

enum active_state parse_active_state(const char *s) {
    if (!s)                              return ACTIVE_STATE_UNKNOWN;
    if (!strcmp(s, "active"))            return ACTIVE_STATE_ACTIVE;
    if (!strcmp(s, "reloading"))         return ACTIVE_STATE_RELOADING;
    if (!strcmp(s, "inactive"))          return ACTIVE_STATE_INACTIVE;
    if (!strcmp(s, "failed"))            return ACTIVE_STATE_FAILED;
    if (!strcmp(s, "activating"))        return ACTIVE_STATE_ACTIVATING;
    if (!strcmp(s, "deactivating"))      return ACTIVE_STATE_DEACTIVATING;
    return ACTIVE_STATE_UNKNOWN;
}

const char *active_state_to_string(enum active_state v) {
    switch (v) {
        case ACTIVE_STATE_ACTIVE:       return "active";
        case ACTIVE_STATE_RELOADING:    return "reloading";
        case ACTIVE_STATE_INACTIVE:     return "inactive";
        case ACTIVE_STATE_FAILED:       return "failed";
        case ACTIVE_STATE_ACTIVATING:   return "activating";
        case ACTIVE_STATE_DEACTIVATING: return "deactivating";
        default:                        return "unknown";
    }
}

enum load_state parse_load_state(const char *s) {
    if (!s)                            return LOAD_STATE_UNKNOWN;
    if (!strcmp(s, "stub"))            return LOAD_STATE_STUB;
    if (!strcmp(s, "loaded"))          return LOAD_STATE_LOADED;
    if (!strcmp(s, "not-found"))       return LOAD_STATE_NOT_FOUND;
    if (!strcmp(s, "bad-setting"))     return LOAD_STATE_BAD_SETTING;
    if (!strcmp(s, "error"))           return LOAD_STATE_ERROR;
    if (!strcmp(s, "merged"))          return LOAD_STATE_MERGED;
    if (!strcmp(s, "masked"))          return LOAD_STATE_MASKED;
    return LOAD_STATE_UNKNOWN;
}

const char *load_state_to_string(enum load_state v) {
    switch (v) {
        case LOAD_STATE_STUB:        return "stub";
        case LOAD_STATE_LOADED:      return "loaded";
        case LOAD_STATE_NOT_FOUND:   return "not-found";
        case LOAD_STATE_BAD_SETTING: return "bad-setting";
        case LOAD_STATE_ERROR:       return "error";
        case LOAD_STATE_MERGED:      return "merged";
        case LOAD_STATE_MASKED:      return "masked";
        default:                     return "unknown";
    }
}

tracked_unit *tracked_unit_find_by_name(const char *name) {
    if (!name) return NULL;
    for (size_t i = 0; i < tracked_units_count; i++) {
        if (tracked_units[i].in_use &&
            !strcmp(tracked_units[i].name, name)) {
            return &tracked_units[i];
        }
    }
    return NULL;
}

tracked_unit *tracked_unit_find_by_object_path(const char *path) {
    if (!path) return NULL;
    for (size_t i = 0; i < tracked_units_count; i++) {
        if (tracked_units[i].in_use &&
            tracked_units[i].attached &&
            !strcmp(tracked_units[i].object_path, path)) {
            return &tracked_units[i];
        }
    }
    return NULL;
}

int tracked_units_register(const char **names, size_t n) {
    if (!names) return -EINVAL;
    if (n > LIMIT_TRACKED_UNITS) return -E2BIG;

    for (size_t i = 0; i < n; i++) {
        if (!names[i]) return -EINVAL;
        if (strlen(names[i]) >= LIMIT_UNIT_NAME) return -ENAMETOOLONG;

        tracked_unit *u = &tracked_units[i];
        memset(u, 0, sizeof(*u));
        u->in_use = true;
        u->attached = false;
        strncpy(u->name, names[i], LIMIT_UNIT_NAME - 1);
        u->name[LIMIT_UNIT_NAME - 1] = '\0';
        u->active_state = ACTIVE_STATE_UNKNOWN;
        u->load_state   = LOAD_STATE_UNKNOWN;
    }
    tracked_units_count = n;
    return 0;
}

// Read a single property on a unit object and assign it to the right cache
// field. Called during the initial snapshot and as a fallback when
// PropertiesChanged lists a property under "invalidated_properties" (meaning
// the server declined to include the new value inline, so we must Get it).
static int tracked_unit_fetch_one_property(tracked_unit *u, const char *property_name) {

    dbus_reset_io();

    // All the properties we track live on either the Unit or Service
    // interface. Try Unit first (covers ActiveState/SubState/LoadState/
    // UnitFileState/StartLimitBurst), then Service (covers
    // ExecMainPID/ExecMainStatus/Result/TimeoutStartUSec).
    const char *interface = interface_unit_systemd;
    if (!strcmp(property_name, property_exec_main_pid_systemd)    ||
        !strcmp(property_name, property_exec_main_status_systemd) ||
        !strcmp(property_name, property_result_systemd)           ||
        !strcmp(property_name, property_timeout_start_usec_systemd)) {
        interface = interface_service_systemd;
    }

    // sd_bus_get_property requires the expected variant signature; passing
    // NULL returns -EINVAL. ExecMainPID and StartLimitBurst are "u",
    // ExecMainStatus is "i", TimeoutStartUSec is "t" (uint64), and
    // every other tracked property is a string.
    const char *signature = "s";
    if (!strcmp(property_name, property_exec_main_pid_systemd) ||
        !strcmp(property_name, property_start_limit_burst_systemd)) {
        signature = "u";
    } else if (!strcmp(property_name, property_exec_main_status_systemd)) {
        signature = "i";
    } else if (!strcmp(property_name, property_timeout_start_usec_systemd)) {
        signature = "t";
    }

    int retval = sd_bus_get_property(bus,
                                     destination_systemd,
                                     u->object_path,
                                     interface,
                                     property_name,
                                     &error,
                                     &message,
                                     signature);
    DBUS_ERROR_READ;
    if (retval < 0) return retval;

    // The reply is a single variant. sd_bus_get_property already entered
    // the variant for us, so we read the contained basic value directly.
    if (!strcmp(property_name, property_active_state_systemd)) {
        const char *s = NULL;
        retval = sd_bus_message_read(message, "s", &s);
        if (retval < 0) return retval;
        u->active_state = parse_active_state(s);
    } else if (!strcmp(property_name, property_sub_state_systemd)) {
        const char *s = NULL;
        retval = sd_bus_message_read(message, "s", &s);
        if (retval < 0) return retval;
        strncpy(u->sub_state, s ? s : "", LIMIT_STATE_NAME - 1);
        u->sub_state[LIMIT_STATE_NAME - 1] = '\0';
    } else if (!strcmp(property_name, property_load_state_systemd)) {
        const char *s = NULL;
        retval = sd_bus_message_read(message, "s", &s);
        if (retval < 0) return retval;
        u->load_state = parse_load_state(s);
    } else if (!strcmp(property_name, property_unit_file_state_systemd)) {
        const char *s = NULL;
        retval = sd_bus_message_read(message, "s", &s);
        if (retval < 0) return retval;
        strncpy(u->unit_file_state, s ? s : "", LIMIT_STATE_NAME - 1);
        u->unit_file_state[LIMIT_STATE_NAME - 1] = '\0';
    } else if (!strcmp(property_name, property_exec_main_pid_systemd)) {
        uint32_t v = 0;
        retval = sd_bus_message_read(message, "u", &v);
        if (retval < 0) return retval;
        u->exec_main_pid = v;
    } else if (!strcmp(property_name, property_exec_main_status_systemd)) {
        int32_t v = 0;
        retval = sd_bus_message_read(message, "i", &v);
        if (retval < 0) return retval;
        u->exec_main_status = v;
    } else if (!strcmp(property_name, property_result_systemd)) {
        const char *s = NULL;
        retval = sd_bus_message_read(message, "s", &s);
        if (retval < 0) return retval;
        strncpy(u->result, s ? s : "", LIMIT_STATE_NAME - 1);
        u->result[LIMIT_STATE_NAME - 1] = '\0';
    } else if (!strcmp(property_name, property_timeout_start_usec_systemd)) {
        uint64_t v = 0;
        retval = sd_bus_message_read(message, "t", &v);
        if (retval < 0) return retval;
        u->timeout_start_usec = v;
    } else if (!strcmp(property_name, property_start_limit_burst_systemd)) {
        uint32_t v = 0;
        retval = sd_bus_message_read(message, "u", &v);
        if (retval < 0) return retval;
        u->start_limit_burst = v;
    }

    return 0;
}

int tracked_unit_fetch_all_properties(tracked_unit *u) {
    if (!u || !u->attached) return -EINVAL;

    // Unit-interface properties apply to every unit type. Type-specific
    // properties are fetched separately based on the unit's suffix so
    // we never ask a .target for Service-only fields (which would return
    // UnknownProperty) or vice versa.
    static const char *const unit_props[] = {
        "ActiveState",
        "SubState",
        "LoadState",
        "UnitFileState",
        "StartLimitBurst",
    };
    static const char *const service_props[] = {
        "ExecMainPID",
        "ExecMainStatus",
        "Result",
        "TimeoutStartUSec",
    };
    // org.freedesktop.systemd1.Target is a marker interface with no
    // distinct properties — a target is fully described by the Unit
    // interface above. The list is kept for structural symmetry with
    // the service branch so future target-specific fields have an
    // obvious home.
    static const char *const target_props[] = { NULL };
    static const size_t target_props_count = 0;

    for (size_t i = 0; i < sizeof(unit_props)/sizeof(unit_props[0]); i++) {
        tracked_unit_fetch_one_property(u, unit_props[i]);
    }

    size_t name_len = strlen(u->name);
    bool is_service = name_len >= 8 &&
                      !strcmp(u->name + name_len - 8, ".service");
    bool is_target  = name_len >= 7 &&
                      !strcmp(u->name + name_len - 7, ".target");

    if (is_service) {
        for (size_t i = 0; i < sizeof(service_props)/sizeof(service_props[0]); i++) {
            tracked_unit_fetch_one_property(u, service_props[i]);
        }
    } else if (is_target) {
        for (size_t i = 0; i < target_props_count; i++) {
            tracked_unit_fetch_one_property(u, target_props[i]);
        }
    }
    return 0;
}

// Resolve a tracked unit's name to its systemd object path via LoadUnit.
// LoadUnit is used rather than GetUnit because GetUnit only returns paths
// for units currently held in the daemon's memory; after enable + reload,
// an unreferenced unit is garbage-collected and GetUnit answers
// NoSuchUnit. LoadUnit both loads-if-needed and returns the object path
// in one round trip. We still treat failure as non-fatal (DBUS_ERROR_CLEAR)
// because a unit whose file is missing will legitimately return an error
// and UnitNew will re-drive attach if/when the unit appears later.
static int tracked_unit_resolve_object_path(tracked_unit *u) {
    dbus_reset_io();
    int retval = sd_bus_call_method(bus,
                                    destination_systemd,
                                    path_systemd,
                                    interface_manager_systemd,
                                    member_load_unit_systemd,
                                    &error,
                                    &message,
                                    "s",
                                    u->name);
    DBUS_ERROR_CLEAR;
    if (retval < 0) return retval;

    const char *path = NULL;
    retval = sd_bus_message_read(message, "o", &path);
    if (retval < 0 || !path) return retval < 0 ? retval : -EPROTO;
    strncpy(u->object_path, path, LIMIT_OBJECT_PATH - 1);
    u->object_path[LIMIT_OBJECT_PATH - 1] = '\0';
    return 0;
}

// Install the per-unit PropertiesChanged match. Thin wrapper around the
// existing listener to give the attach orchestrator a named step.
static int tracked_unit_subscribe_changes(tracked_unit *u) {
    return dbus_property_changed_listener(u);
}

// Populate every cached field from the live object. Equivalent to
// tracked_unit_fetch_all_properties, renamed at the attach-orchestrator
// layer to read as "take the initial snapshot".
static int tracked_unit_snapshot(tracked_unit *u) {
    return tracked_unit_fetch_all_properties(u);
}

int tracked_unit_attach(tracked_unit *u) {
    if (!u || !u->in_use) return -EINVAL;
    if (u->attached) return 0;

    int retval = tracked_unit_resolve_object_path(u);
    if (retval < 0) return retval;

    // Subscribe BEFORE snapshot so a PropertiesChanged arriving between
    // the two steps isn't lost. fetch_all asserts attached, so flip the
    // flag first; roll it back on subscribe failure.
    u->attached = true;
    retval = tracked_unit_subscribe_changes(u);
    if (retval < 0) {
        u->attached = false;
        u->slot_properties_changed = sd_bus_slot_unref(u->slot_properties_changed);
        u->object_path[0] = '\0';
        return retval;
    }

    tracked_unit_snapshot(u);

    // Fire the state-changed callback with the starting point so callers
    // see the initial snapshot on the same pipe future updates arrive on.
    if (state_changed_cb) state_changed_cb(u, "(initial)", state_changed_ud);

    return 0;
}

void tracked_unit_detach(tracked_unit *u) {
    if (!u) return;
    u->slot_properties_changed = sd_bus_slot_unref(u->slot_properties_changed);
    u->attached = false;
    u->object_path[0] = '\0';
    // Property cache is left as-is; it reflects the last known state before
    // removal, which on_unit_state_changed callers may still want to read.
}

int tracked_units_attach_all(void) {
    for (size_t i = 0; i < tracked_units_count; i++) {
        if (!tracked_units[i].in_use) continue;
        tracked_unit_attach(&tracked_units[i]);
        // Ignore return: units not yet loaded will attach later via UnitNew.
    }
    return 0;
}

int tracked_units_reset_failed_all(void) {
    // Clear sticky "failed" state on every tracked unit at init. Errors
    // here are expected and silently swallowed inside dbus_reset_failed_unit
    // because units that are not currently loaded will legitimately return
    // NoSuchUnit. Called after register + daemon reload, before attach, so
    // the initial snapshot reflects the cleaned state.
    for (size_t i = 0; i < tracked_units_count; i++) {
        if (!tracked_units[i].in_use) continue;
        dbus_reset_failed_unit(tracked_units[i].name);
    }
    return 0;
}

// PropertiesChanged handler. Signature of the signal body is "sa{sv}as":
//   s      = interface whose properties changed
//   a{sv}  = changed properties, as name -> variant
//   as     = invalidated property names (server says "changed but I'm not
//            telling you the new value; Get it yourself")
int dbus_properties_changed_handler(sd_bus_message *m, void *userdata, sd_bus_error *ret_error) {
    (void) ret_error;
    tracked_unit *u = (tracked_unit *) userdata;
    if (!u) return 0;

    const char *changed_interface = NULL;
    int retval = sd_bus_message_read(m, "s", &changed_interface);
    if (retval < 0) return retval;

    // Enter the a{sv} dict.
    retval = sd_bus_message_enter_container(m, SD_BUS_TYPE_ARRAY, "{sv}");
    if (retval < 0) return retval;

    while ((retval = sd_bus_message_enter_container(m, SD_BUS_TYPE_DICT_ENTRY, "sv")) > 0) {
        const char *property_name = NULL;
        retval = sd_bus_message_read(m, "s", &property_name);
        if (retval < 0) return retval;

        // Peek the variant's contained type so we can read the right basic.
        char type = 0;
        const char *contents = NULL;
        retval = sd_bus_message_peek_type(m, &type, &contents);
        if (retval < 0) return retval;

        retval = sd_bus_message_enter_container(m, SD_BUS_TYPE_VARIANT, contents);
        if (retval < 0) return retval;

        bool updated = false;

        if (!strcmp(property_name, property_active_state_systemd) && contents && contents[0] == 's') {
            const char *s = NULL;
            if (sd_bus_message_read(m, "s", &s) >= 0) {
                u->active_state = parse_active_state(s);
                updated = true;
            }
        } else if (!strcmp(property_name, property_sub_state_systemd) && contents && contents[0] == 's') {
            const char *s = NULL;
            if (sd_bus_message_read(m, "s", &s) >= 0) {
                strncpy(u->sub_state, s ? s : "", LIMIT_STATE_NAME - 1);
                u->sub_state[LIMIT_STATE_NAME - 1] = '\0';
                updated = true;
            }
        } else if (!strcmp(property_name, property_load_state_systemd) && contents && contents[0] == 's') {
            const char *s = NULL;
            if (sd_bus_message_read(m, "s", &s) >= 0) {
                u->load_state = parse_load_state(s);
                updated = true;
            }
        } else if (!strcmp(property_name, property_unit_file_state_systemd) && contents && contents[0] == 's') {
            const char *s = NULL;
            if (sd_bus_message_read(m, "s", &s) >= 0) {
                strncpy(u->unit_file_state, s ? s : "", LIMIT_STATE_NAME - 1);
                u->unit_file_state[LIMIT_STATE_NAME - 1] = '\0';
                updated = true;
            }
        } else if (!strcmp(property_name, property_exec_main_pid_systemd) && contents && contents[0] == 'u') {
            uint32_t v = 0;
            if (sd_bus_message_read(m, "u", &v) >= 0) {
                u->exec_main_pid = v;
                updated = true;
            }
        } else if (!strcmp(property_name, property_exec_main_status_systemd) && contents && contents[0] == 'i') {
            int32_t v = 0;
            if (sd_bus_message_read(m, "i", &v) >= 0) {
                u->exec_main_status = v;
                updated = true;
            }
        } else if (!strcmp(property_name, property_result_systemd) && contents && contents[0] == 's') {
            const char *s = NULL;
            if (sd_bus_message_read(m, "s", &s) >= 0) {
                strncpy(u->result, s ? s : "", LIMIT_STATE_NAME - 1);
                u->result[LIMIT_STATE_NAME - 1] = '\0';
                updated = true;
            }
        } else {
            // Property we don't track, or unexpected variant type. Skip
            // the variant contents so the message cursor stays aligned.
            sd_bus_message_skip(m, contents);
        }

        sd_bus_message_exit_container(m); // variant
        sd_bus_message_exit_container(m); // dict entry

        if (updated && state_changed_cb) {
            state_changed_cb(u, property_name, state_changed_ud);
        }
    }
    sd_bus_message_exit_container(m); // a{sv}

    // Walk the invalidated-properties list and re-Get each one we care about.
    retval = sd_bus_message_enter_container(m, SD_BUS_TYPE_ARRAY, "s");
    if (retval < 0) return retval;

    const char *invalidated = NULL;
    while ((retval = sd_bus_message_read(m, "s", &invalidated)) > 0) {
        // tracked_unit_fetch_one_property calls dbus_reset_io(), which will
        // unref the signal message `m` we are currently iterating IF it is
        // the global. It isn't: `m` here is the handler-scoped message
        // sd-bus owns, not the global `message`. Safe to call.
        if (!strcmp(invalidated, property_active_state_systemd)     ||
            !strcmp(invalidated, property_sub_state_systemd)        ||
            !strcmp(invalidated, property_load_state_systemd)       ||
            !strcmp(invalidated, property_unit_file_state_systemd)  ||
            !strcmp(invalidated, property_exec_main_pid_systemd)    ||
            !strcmp(invalidated, property_exec_main_status_systemd) ||
            !strcmp(invalidated, property_result_systemd)) {
            if (tracked_unit_fetch_one_property(u, invalidated) >= 0 &&
                state_changed_cb) {
                state_changed_cb(u, invalidated, state_changed_ud);
            }
        }
    }
    sd_bus_message_exit_container(m); // as

    (void) changed_interface;
    return 0;
}
