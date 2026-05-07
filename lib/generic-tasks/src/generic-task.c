#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
#include <time.h>
#include <string.h>

#ifndef PROCESS_NUM
#define PROCESS_NUM -1
#endif

#ifndef PROCESS_COUNT
#define PROCESS_COUNT -1
#endif

#define FILE_TO_WRITE stdout
#define FILE_NAME_LIMIT 255

typedef enum {
    BARE,
    CONTAINER
} exec_env;

int process_count = 0;
int opened_processes[PROCESS_COUNT];
int dependency_count = 0;
int dependencies[PROCESS_COUNT];

exec_env environment;

void sig_handler(int signum);

int generic_task();

int main(int argc, char *argv[]) {

    signal(SIGABRT, sig_handler);
    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);
    signal(SIGSEGV, sig_handler);

    if (PROCESS_NUM == -1) {

        fprintf(stderr, "PROCESS_NUM is not defined. Define it while compilation using -DPROCESS_NUM=<some-number> flag.");
        exit(1);

    }

    if (PROCESS_COUNT == -1) {

        fprintf(stderr, "PROCESS_COUNT is not defined. Define it while compilation using -DPROCESS_COUNT=<some-number> flag.");
        exit(1);

    }

    if (argc == 1) {
        environment = BARE;
    } else if (argc > 2) {

        if (!strcmp(argv[1], "CONTAINER")) {
            environment = CONTAINER;
        } else if (!strcmp(argv[1], "BARE")) {
            environment = BARE;
        } else {
            environment = BARE;
        }
    }

    clock_t start = clock();
    clock_t finish;

    // GENERIC TASK BEGIN

    generic_task();

    // GENERIC TASK END

    finish = clock() - start;
    double total_time = (double)finish / (double)CLOCKS_PER_SEC;

    return 0;

}

int generic_task() {
    while(1) sleep(1);
}


void sig_handler(int signum) {
    exit(signum);
}
