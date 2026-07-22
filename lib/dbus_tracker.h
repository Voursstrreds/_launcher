#ifndef DBUS_TRACKER_H
#define DBUS_TRACKER_H

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <limits.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdarg.h>
#include <systemd/sd-bus.h>
#include "constant_limits.h"

// Logs the current error (if retval is negative) and frees it. Falls back to
// strerror(-retval) when the sd_bus_error struct is empty, which happens for
// sd-bus functions that do not take an sd_bus_error* out-parameter.
// __func__ expands inside the calling function's scope, so the printed
// "in <name>" identifies the wrapper that triggered the error.
// Does NOT return; callers must check retval themselves and decide what to do.
#define DBUS_ERROR_READ \
do { \
    if (retval < 0) { \
        if (error.name) { \
            printf("[%s] Error name: %s\nError message: %s\n", \
                   __func__, error.name, error.message); \
        } else { \
            printf("[%s] Error name: (none)\nError message: %s\n", \
                   __func__, strerror(-retval)); \
        } \
        sd_bus_error_free(&error); \
    } \
} while (0)

// Same contract as DBUS_ERROR_READ but silent. Use in wrappers whose failure
// is an expected, non-fatal condition (e.g. GetUnit / ResetFailedUnit on a
// unit that is not currently loaded) so the log isn't spammed with
// NoSuchUnit errors every startup.
#define DBUS_ERROR_CLEAR \
do { \
    if (retval < 0) { \
        sd_bus_error_free(&error); \
    } \
} while (0)

extern sd_bus *bus;
extern sd_bus_slot *slot;
extern sd_bus_message *message;
extern sd_bus_error error;

extern sd_bus_slot *slot_unit_new;
extern sd_bus_slot *slot_unit_removed;
extern sd_bus_slot *slot_property_changed;
extern sd_bus_slot *slot_reloading;

extern const char *name_register;
extern const char *path_register;
extern const char *interface_name_register;
extern const char *interface_path_register;

extern const char *destination_systemd;
extern const char *path_systemd;

extern const char *interface_manager_systemd;
extern const char *interface_service_systemd;
extern const char *interface_job_systemd;
extern const char *interface_unit_systemd;

extern const char *signal_reload;
extern const char *signal_unit_new;
extern const char *signal_unit_removed;
extern const char *signal_property_changed;

extern const char *member_subscribe_systemd;
extern const char *member_unsubscribe_systemd;
extern const char *member_load_unit_systemd;
extern const char *member_start_unit_systemd;
extern const char *member_stop_unit_systemd;
extern const char *member_reload_unit_systemd;
extern const char *member_restart_unit_systemd;
extern const char *member_reload_systemd;
extern const char *member_start_transient_unit_systemd;
extern const char *member_service_property_execstart_systemd;
extern const char *member_get_unit_systemd;
extern const char *member_enable_unit_file_systemd;
extern const char *member_disable_unit_file_systemd;
extern const char *member_reset_failed_unit_systemd;
extern const char *member_reset_failed_systemd;

extern const char *interface_properties_dbus;
extern const char *signal_properties_changed_dbus;
extern const char *member_get_dbus;

extern const char *property_active_state_systemd;
extern const char *property_sub_state_systemd;
extern const char *property_load_state_systemd;
extern const char *property_unit_file_state_systemd;
extern const char *property_exec_main_pid_systemd;
extern const char *property_exec_main_status_systemd;
extern const char *property_result_systemd;
extern const char *property_timeout_start_usec_systemd;
extern const char *property_start_limit_burst_systemd;



enum signal_type {
    RELOADING,
    UNIT_NEW,
    UNIT_REMOVED,
    PROPERTY_CHANGED
};

// ActiveState has a closed set of values per systemd docs.
enum active_state {
    ACTIVE_STATE_UNKNOWN = 0,
    ACTIVE_STATE_ACTIVE,
    ACTIVE_STATE_RELOADING,
    ACTIVE_STATE_INACTIVE,
    ACTIVE_STATE_FAILED,
    ACTIVE_STATE_ACTIVATING,
    ACTIVE_STATE_DEACTIVATING
};

// LoadState has a closed set of values per systemd docs.
enum load_state {
    LOAD_STATE_UNKNOWN = 0,
    LOAD_STATE_STUB,
    LOAD_STATE_LOADED,
    LOAD_STATE_NOT_FOUND,
    LOAD_STATE_BAD_SETTING,
    LOAD_STATE_ERROR,
    LOAD_STATE_MERGED,
    LOAD_STATE_MASKED
};

// One tracked unit. Fixed storage, owned by the tracked_units array.
// SubState, UnitFileState, and Result are left as strings because their
// value sets are open-ended or unit-type-dependent.
typedef struct tracked_unit {
    bool in_use;                    // slot occupied
    bool attached;                  // GetUnit succeeded and PropertiesChanged is subscribed

    char name[LIMIT_UNIT_NAME];
    char object_path[LIMIT_OBJECT_PATH];

    sd_bus_slot *slot_properties_changed;

    enum active_state active_state;
    char              sub_state[LIMIT_STATE_NAME];
    enum load_state   load_state;
    char              unit_file_state[LIMIT_STATE_NAME];
    uint32_t          exec_main_pid;
    int32_t           exec_main_status;
    char              result[LIMIT_STATE_NAME];

    // Deadline inputs. Populated once at attach from the running unit, then
    // never refreshed (these don't change at runtime — only on daemon-reload,
    // which we always perform before attach). UINT64_MAX for the timeout
    // signals systemd's "infinity"; 0 for start_limit_burst signals "no
    // limit". Callers handle those sentinels as "no deadline for this unit".
    uint64_t          timeout_start_usec;
    uint32_t          start_limit_burst;
} tracked_unit;

extern tracked_unit tracked_units[LIMIT_TRACKED_UNITS];
extern size_t tracked_units_count;

extern const sd_bus_vtable vtable[];

int dbus_init();
int dbus_tear_down();

void dbus_reset_io(void);

int dbus_subscribe_systemd();
int dbus_unsubscribe_systemd();


int dbus_daemon_reload_listener();
int dbus_unit_new_listener();
int dbus_unit_removed_listener();
int dbus_property_changed_listener(tracked_unit *u);

int dbus_daemon_reload_sender();


int dbus_start_unit(const char *unit_name, const char *mode);
int dbus_stop_unit(const char *unit_name, const char *mode);
int dbus_load_unit(const char *unit_name);
int dbus_reload_unit(const char *unit_name);
int dbus_restart_unit(const char *unit_name);
int dbus_enable_unit(const char *unit_name);
int dbus_disable_unit(const char *unit_name);
int dbus_reset_failed_unit(const char *unit_name);
int dbus_reset_failed_all(void);


int dbus_wait(uint64_t timeout_usec);
int dbus_process(sd_bus_message **ret_message);


int dbus_get_unit(const char *unit_name);
int dbus_get_property(const char *unit_object_path, const char *interface, const char *member, const char *signature);

int dbus_signal_handler(sd_bus_message *m, void *userdata, sd_bus_error *ret_error);
int dbus_properties_changed_handler(sd_bus_message *m, void *userdata, sd_bus_error *ret_error);

// Tracked units: registration, attach/detach, property fetching.
int  tracked_units_register(const char **names, size_t n);
int  tracked_units_attach_all(void);
int  tracked_units_reset_failed_all(void);
int  tracked_unit_attach(tracked_unit *u);
void tracked_unit_detach(tracked_unit *u);
int  tracked_unit_fetch_all_properties(tracked_unit *u);
tracked_unit *tracked_unit_find_by_name(const char *name);
tracked_unit *tracked_unit_find_by_object_path(const char *path);

// Enum parsers for closed-set systemd property strings.
enum active_state parse_active_state(const char *s);
enum load_state   parse_load_state(const char *s);
const char *active_state_to_string(enum active_state v);
const char *load_state_to_string(enum load_state v);

// State-changed callback. Invoked once per tracked unit during initial
// snapshot (property_name == "(initial)") and once per subsequent
// PropertiesChanged update. The `u` pointer is valid for the duration
// of the call only; do not store it.
typedef void (*tracked_unit_state_changed_cb)(
    const tracked_unit *u,
    const char *property_name,
    void *userdata);

void tracked_units_set_state_changed_cb(
    tracked_unit_state_changed_cb cb,
    void *userdata);

#endif // DBUS_TRACKER_H
