#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
#include <limits.h>
#include <time.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>

#ifndef GROUP_NUM
#define GROUP_NUM -1
#endif

#ifndef PROCESS_COUNT
#define PROCESS_COUNT -1
#endif

#ifndef GROUP_COUNT
#define GROUP_COUNT -1
#endif

#define FILE_TO_WRITE stdout
#define FILE_NAME_LIMIT 255

typedef enum {
    BARE,
    CONTAINER
} exec_env;

int processes_in_group_count = 0;
int processes_in_group[PROCESS_COUNT];
int group_count = 0;
int group_list[GROUP_COUNT];
int opened_group_count = 0;
int opened_groups[GROUP_COUNT];

char file_name[FILE_NAME_LIMIT];

exec_env environment;

void sig_handler(int signum);

int group_exec();

int main(int argc, char *argv[]) {

    signal(SIGABRT, sig_handler);
    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);
    signal(SIGSEGV, sig_handler);

    if (GROUP_NUM == -1) {

        fprintf(stderr, "GROUP_NUM is not defined. Define it while compilation using -DGROUP_NUM=<some-number> flag.");
        exit(1);

    }

    if (PROCESS_COUNT == -1) {

        fprintf(stderr, "PROCESS_COUNT is not defined. Define it while compilation using -DPROCESS_COUNT=<some-number> flag.");
        exit(1);

    }

    if (GROUP_COUNT == -1) {

        fprintf(stderr, "GROUP_COUNT is not defined. Define it while compilation using -DGROUP_COUNT=<some-number> flag.");
        exit(1);

    }

    sprintf(file_name, "%s", argv[0]);

    if (argc == 1) {
        environment = BARE;
    } else if (argc > 2) {

        if (!strcmp(argv[1], "CONTAINER")) {
            environment = CONTAINER;
        } else if (!strcmp(argv[1], "BARE")) {
            environment = BARE;
        } else {
            fprintf(stderr, "Invalid argument. Exiting.\n");
            exit(1);
        }

        int i;
        for (i = 2; i < argc; i++) {
            if (argv[i][0] == 'P') {
                processes_in_group[processes_in_group_count++] = atoi(argv[i]+1);
            } else if (argv[i][0] == 'G') {
                group_list[group_count++] = atoi(argv[i]+1);
            }
        }
    }

    clock_t start = clock();
    clock_t finish;

    // GENERIC GROUP BEGIN

    group_exec();

    // GENERIC GROUP END

    finish = clock() - start;
    double total_time = (double)finish / (double)CLOCKS_PER_SEC;

    return 0;

}

int group_exec() {

    pid_t *processes = (pid_t*)malloc(sizeof(pid_t) * processes_in_group_count);

    int i, status;

    int last_slash_position;
    for (i = FILE_NAME_LIMIT-1; i >= 0; i--) {
        if (file_name[i] == '/') {
            last_slash_position = i;
            break;
        }
    }

    for (i = 0; i < processes_in_group_count; i++) {
        sprintf(file_name + last_slash_position + 1, "generic-task-%d", processes_in_group[i]);

        processes[i] = fork();
        if (processes[i] == 0) {
            if (environment == BARE) {
                execl(file_name, "BARE", NULL);
            } else if (environment == CONTAINER) {
                execl(file_name, "CONTAINER", NULL);
            }
        } else if (processes[i] > 0) {
            continue;
        }
    }

    for (i = 0; i < processes_in_group_count; i++) {
        waitpid(processes[i], &status, 0);
    }

    free(processes);

    return 0;

}

void sig_handler(int signum) {
    exit(signum);
}
