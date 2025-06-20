#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include <sys/epoll.h>
#include <sys/types.h>
#include <signal.h>
#include <time.h>
#include <pthread.h>
#include <dirent.h>
#include "daemonize.h"


// ============================================================================


extern void log2file(const char *fmt, ...);


int get_work_path(char* buff, int maxlen);

int init_gpio(int gpio, char* edge);
void release_gpio(int gpio);

int load_python_view();
void send_signal_to_python_process(int signal);


// ============================================================================


static int gpio_d0=0, gpio_d1=2, gpio_d2=3;
static int epfd=-1;
static int fd_d0=-1, fd_d1=-1, fd_d2=-1;


// ============================================================================


int main(int argc, char* argv[]) {
    char workpath[255];
    struct epoll_event ev_d0, ev_d1, ev_d2;
    struct epoll_event events[10];
    unsigned int value = 0;
    unsigned int k1 = 0,k2 = 0,k3 = 0;
    int i, n;
    char ch;

    if (isAlreadyRunning() == 1) {
        exit(3);
    }
    daemonize("nanohat-oled");

    int ret = get_work_path(workpath, sizeof(workpath));
    if (ret != 0) {
        log2file("get_work_path ret error\n");
        return 1;
    }
    sleep(3);

    epfd = epoll_create(1);
    if (epfd < 0) {
        log2file("error creating epoll\n");
        return 1;
    }

    fd_d0 = init_gpio(gpio_d0, "rising");
    if (fd_d0 < 0) {
        log2file("error opening gpio sysfs entries\n");
        return 1;
    }

    fd_d1 = init_gpio(gpio_d1, "rising");
    if (fd_d1  < 0) {
        log2file("error opening gpio sysfs entries\n");
        return 1;
    }

    fd_d2 = init_gpio(gpio_d2, "rising");
    if (fd_d2 < 0) {
        log2file("error opening gpio sysfs entries\n");
        return 1;
    }

    ev_d0.events = EPOLLET;
    ev_d1.events = EPOLLET;
    ev_d2.events = EPOLLET;
    ev_d0.data.fd = fd_d0;
    ev_d1.data.fd = fd_d1;
    ev_d2.data.fd = fd_d2;

    n = epoll_ctl(epfd, EPOLL_CTL_ADD, fd_d0, &ev_d0);
    if (n != 0) {
        log2file("epoll_ctl returned %d: %s\n", n, strerror(errno));
        return 1;
    }

    n = epoll_ctl(epfd, EPOLL_CTL_ADD, fd_d1, &ev_d1);
    if (n != 0) {
        log2file("epoll_ctl returned %d: %s\n", n, strerror(errno));
        return 1;
    }

    n = epoll_ctl(epfd, EPOLL_CTL_ADD, fd_d2, &ev_d2);
    if (n != 0) {
        log2file("epoll_ctl returned %d: %s\n", n, strerror(errno));
        return 1;
    }

    load_python_view(workpath);
    while (1) {
        n = epoll_wait(epfd, events, 10, 15);

        for (i = 0; i < n; ++i) {
            if (events[i].data.fd == ev_d0.data.fd) {
                lseek(fd_d0, 0, SEEK_SET);
                if (read(fd_d0, &ch, 1)>0) {
                    log2file("k1 events: %c\n", ch);

                    if (ch == '1') {
                        send_signal_to_python_process(SIGUSR1);
                    }
                }
            } else if (events[i].data.fd == ev_d1.data.fd) {
                lseek(fd_d1, 0, SEEK_SET);
                if (read(fd_d1, &ch, 1)>0) {
                    log2file("k2 events: %c\n", ch);

                    if (ch == '1') {
                        send_signal_to_python_process(SIGUSR2);
                    }
                }
            } else if (events[i].data.fd == ev_d2.data.fd) {
                lseek(fd_d2, 0, SEEK_SET);
                if (read(fd_d2, &ch, 1)>0) {
                    log2file("k3 events: %c\n", ch);
                    if (ch == '1') {
                        send_signal_to_python_process(SIGALRM);
                    }
                }
            }
        }
    }

    return 0;
}


//// search /proc/self/exe and get process work path
int get_work_path(char* buff, int maxlen) {
    ssize_t len = readlink("/proc/self/exe", buff, maxlen);
    if (len == -1 || len == maxlen) {
        return -1;
    }
    buff[len] = '\0';

    char *pos = strrchr(buff, '/');
    if (pos != 0) {
       *pos = '\0';
    }
    return 0;
}


//// initialize gpio fd
int init_gpio(int gpio, char* edge) {
    char path[42];
    FILE *fp;
    int fd;

    // export gpio to userspace
    fp = fopen("/sys/class/gpio/export", "w");
    if (fp) {
        fprintf(fp, "%d\n", gpio);
        fclose(fp);
    }

    // set output direction
    sprintf(path, "/sys/class/gpio/gpio%d/direction", gpio);
    fp = fopen(path, "w");
    if (fp) {
        fprintf(fp, "%s\n", "in");
        fclose(fp);
    }

    // falling edge
    sprintf(path, "/sys/class/gpio/gpio%d/edge", gpio);
    fp = fopen(path, "w");
    if (fp) {
        fprintf(fp, "%s\n", edge);
        fclose(fp);
    }

    sprintf(path, "/sys/class/gpio/gpio%d/value", gpio);
    fd = open(path, O_RDWR | O_NONBLOCK);
    if (fd < 0) {
        log2file("open of gpio %d returned %d: %s\n",
                gpio, fd, strerror(errno));
    }

    return fd;
}

//// close gpio and fd
void release_gpio(int gpio) {
    FILE* fp = fopen("/sys/class/gpio/unexport", "w");
    if (fp) {
        fprintf(fp, "%d\n", gpio);
        fclose(fp);
    }
}


//// according signal to handle gpio fd
//// NOTE: just a demo, but NOT use the function in this main.c
void sig_handler(int sig)
{
    if(sig == SIGINT){
        if (epfd>=0) {
            close(epfd);
        }
        if (fd_d0>=0) {
            close(fd_d0);
            release_gpio(gpio_d0);
        }
        if (fd_d1>=0) {
            close(fd_d1);
            release_gpio(gpio_d1);
        }
        if (fd_d2>=0) {
            close(fd_d2);
            release_gpio(gpio_d2);
        }
        log2file("ctrl+c has been keydown\n");
        exit(0);
    }
}


// ============================================================================


//// pthread preparation
pthread_t view_thread_id = 0;
void* threadfunc(char* arg) {
    pthread_detach(pthread_self());
    if (arg) {
        char* cmd = arg;
        system(cmd);
        free(arg);
    }
}

//// threading python view
int load_python_view(char *workpath) {
    int ret;
    char* cmd = (char*)malloc(255);
    sprintf(cmd, "cd %s/BakeBit/Software/Python && python3 %s 2>&1 | tee /tmp/nanoled-python.log", workpath, PYTHON3_SCRIPT);
    ret = pthread_create(&view_thread_id, NULL, (void*)threadfunc, cmd);
    if(ret) {
        log2file("create pthread error \n");
        return 1;
    }
    return 0;
}


// ============================================================================


//// find and get the pid in /proc by process name with exact match.
int find_pid_by_name( char* ProcName, int* foundpid) {
    DIR             *dir;
    struct dirent   *d;
    int             pid, i;
    char            *s;
    int             pnlen;

    i = 0;
    foundpid[0] = 0;
    pnlen = strlen(ProcName);

    /* Open the /proc directory. */
    dir = opendir("/proc");
    if (!dir)
    {
        log2file("cannot open /proc");
        return -1;
    }

    /* Walk through the directory. */
    while ((d = readdir(dir)) != NULL) {

        char exe [PATH_MAX+1];
        char path[PATH_MAX+1];
        int len;
        int namelen;

        /* See if this is a process */
        if ((pid = atoi(d->d_name)) == 0) {
            continue;
        }

        snprintf(exe, sizeof(exe), "/proc/%s/exe", d->d_name);
        if ((len = readlink(exe, path, PATH_MAX)) < 0) {
            continue;
        }
        path[len] = '\0';

        /* Find ProcName */
        s = strrchr(path, '/');
        if (s == NULL) {
            continue;
        }
        s++;

        /* we don't need small name len */
        namelen = strlen(s);
        if (namelen < pnlen) {
            continue;
        }

        if (!strncmp(ProcName, s, pnlen)) {
            /* to avoid subname like search proc tao but proc taolinke matched */
            if (s[pnlen] == ' ' || s[pnlen] == '\0') {
                if (DEBUG) {
                    log2file("found pid %d\n", pid);
                }
                foundpid[i] = pid;
                i++;
            }
        }
    }
    foundpid[i] = 0;
    closedir(dir);
    return 0;
}


//// find python process pid and send signal to the process
static int py_pids[128];
static int pid_count = 0;
void send_signal_to_python_process(int signal) {
    int i, rv;
    if (pid_count == 0) {
        //rv = find_pid_by_name( "python3.7", py_pids);
        rv = find_pid_by_name(PYTHON3_INTERP, py_pids);
        for(i=0; py_pids[i] != 0; i++) {
            log2file("found python pid: %d\n", py_pids[i]);
            pid_count++;
        }
    }
    if (pid_count > 0) {
        for(i=0; i<pid_count; i++) {
            if (kill(py_pids[i], signal) != 0) { //maybe pid is invalid
                pid_count = 0;
                break;
            }
        }
    }
}

