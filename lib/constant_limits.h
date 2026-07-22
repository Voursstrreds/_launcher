#ifndef CONSTANT_LIMITS_H
#define CONSTANT_LIMITS_H


#define LIMIT_UNIT_NAME 256
#define LIMIT_ENTRY_NAME 256
#define LIMIT_TYPE_NAME 256
#define LIMIT_FILE_BUFFER (1 << 16)
#define READ_WRITE_CHUNK (1 << 12)

/* FailureBehavior buffer size. Used by the file-scope config-default
 * strings; the per-instance/per-mapping behavior is an enum, not a string. */
#define LIMIT_FAILURE_BEHAVIOR 16

#define LIMIT_TRACKED_UNITS 64
#define LIMIT_OBJECT_PATH 256
#define LIMIT_STATE_NAME 64



#endif // CONSTANT_LIMITS_H
