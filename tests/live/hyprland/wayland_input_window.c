/* Test-only Wayland receiver. It never injects input or captures the screen. */
#define _POSIX_C_SOURCE 200809L
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "pointer-constraints-v1-client-protocol.h"
#include "relative-pointer-v1-client-protocol.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static struct wl_display *display;
static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct wl_pointer *pointer;
static struct wl_surface *surface;
static struct xdg_wm_base *wm;
static struct xdg_surface *shell_surface;
static struct xdg_toplevel *top;
static struct zwp_pointer_constraints_v1 *constraints;
static struct zwp_relative_pointer_manager_v1 *relative_manager;
static struct zwp_relative_pointer_v1 *relative;
static struct zwp_locked_pointer_v1 *lock;
static struct wl_buffer *buffer;
static void *pixels;
static size_t pixel_bytes;
static int width = 640, height = 480, buffer_width, buffer_height;
static bool entered, busy, redraw;
static uint32_t seat_name;
static volatile sig_atomic_t stopping, failed;
static double stamp(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
#define EVENT(name, fields, ...) do { printf("{\"event\":\"" name "\",\"monotonic\":%.9f" fields "}\n", stamp(), ##__VA_ARGS__); fflush(stdout); } while (0)
static void stop_signal(int sig) { if (sig == SIGALRM) _exit(124); stopping = 1; }
static void fail(const char *message) { fprintf(stderr, "%s\n", message); failed = 1; stopping = 1; }
static void paint(void);
static void released(void *data, struct wl_buffer *b) { (void)data; (void)b; busy = false; if (redraw) paint(); }
static const struct wl_buffer_listener buffer_listener = { .release = released };
static void paint(void) {
    if (busy) { redraw = true; return; }
    redraw = false;
    if (width < 1 || height < 1 || width > 8192 || height > 8192 || (uint64_t)width * height * 4 > 64 * 1024 * 1024) { fail("Invalid buffer dimensions"); return; }
    if (!buffer || width != buffer_width || height != buffer_height) {
        if (buffer) { wl_buffer_destroy(buffer); buffer = NULL; }
        if (pixels) { munmap(pixels, pixel_bytes); pixels = NULL; }
        pixel_bytes = 0;
        char path[] = "/tmp/pcbridge-wayland-buffer-XXXXXX";
        int fd = mkstemp(path);
        if (fd < 0) { fail("Cannot create SHM file"); return; }
        unlink(path);
        pixel_bytes = (size_t)width * height * 4;
        if (ftruncate(fd, (off_t)pixel_bytes) < 0) { close(fd); fail("Cannot size SHM"); return; }
        pixels = mmap(NULL, pixel_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (pixels == MAP_FAILED) { pixels = NULL; close(fd); fail("Cannot map SHM"); return; }
        for (size_t i = 0; i < pixel_bytes / 4; ++i) ((uint32_t *)pixels)[i] = 0xff234553;
        struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, (int)pixel_bytes);
        buffer = wl_shm_pool_create_buffer(pool, 0, width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
        wl_shm_pool_destroy(pool); close(fd);
        wl_buffer_add_listener(buffer, &buffer_listener, NULL);
        buffer_width = width; buffer_height = height;
    }
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage(surface, 0, 0, width, height);
    wl_surface_commit(surface); busy = true;
    EVENT("ready", ",\"width\":%d,\"height\":%d", width, height);
}
static void ping(void *d, struct xdg_wm_base *w, uint32_t serial) { (void)d; xdg_wm_base_pong(w, serial); }
static const struct xdg_wm_base_listener wm_listener = { .ping = ping };
static void configured(void *d, struct xdg_surface *s, uint32_t serial) { (void)d; xdg_surface_ack_configure(s, serial); paint(); }
static const struct xdg_surface_listener surface_listener = { .configure = configured };
static void resized(void *d, struct xdg_toplevel *t, int32_t w, int32_t h, struct wl_array *states) { (void)d; (void)t; (void)states; if (w > 0) width = w; if (h > 0) height = h; }
static void closed(void *d, struct xdg_toplevel *t) { (void)d; (void)t; stopping = 1; }
static const struct xdg_toplevel_listener top_listener = { .configure = resized, .close = closed };
static void enter(void *d, struct wl_pointer *p, uint32_t serial, struct wl_surface *s, wl_fixed_t x, wl_fixed_t y) { (void)d; (void)p; (void)serial; entered = s == surface; EVENT("enter", ",\"x\":%.5f,\"y\":%.5f", wl_fixed_to_double(x), wl_fixed_to_double(y)); }
static void leave(void *d, struct wl_pointer *p, uint32_t serial, struct wl_surface *s) { (void)d; (void)p; (void)serial; (void)s; entered = false; EVENT("leave", ""); }
static void motion(void *d, struct wl_pointer *p, uint32_t time, wl_fixed_t x, wl_fixed_t y) { (void)d; (void)p; (void)time; EVENT("motion", ",\"x\":%.5f,\"y\":%.5f", wl_fixed_to_double(x), wl_fixed_to_double(y)); }
static void button(void *d, struct wl_pointer *p, uint32_t serial, uint32_t time, uint32_t b, uint32_t state) { (void)d; (void)p; (void)serial; (void)time; EVENT("button", ",\"button\":%u,\"state\":%u", b, state); }
static void axis(void *d, struct wl_pointer *p, uint32_t time, uint32_t a, wl_fixed_t value) { (void)d; (void)p; (void)time; EVENT("axis", ",\"axis\":%u,\"value\":%.5f", a, wl_fixed_to_double(value)); }
static void frame(void *d, struct wl_pointer *p) { (void)d; (void)p; }
static void source(void *d, struct wl_pointer *p, uint32_t s) { (void)d; (void)p; (void)s; }
static void axis_stop(void *d, struct wl_pointer *p, uint32_t t, uint32_t a) { (void)d; (void)p; (void)t; (void)a; }
static void discrete(void *d, struct wl_pointer *p, uint32_t a, int32_t v) { (void)d; (void)p; (void)a; (void)v; }
static const struct wl_pointer_listener pointer_listener = { .enter=enter, .leave=leave, .motion=motion, .button=button, .axis=axis, .frame=frame, .axis_source=source, .axis_stop=axis_stop, .axis_discrete=discrete };
static void relative_motion(void *d, struct zwp_relative_pointer_v1 *p, uint32_t hi, uint32_t lo, wl_fixed_t x, wl_fixed_t y, wl_fixed_t ux, wl_fixed_t uy) { (void)d; (void)p; EVENT("relative", ",\"protocol_time_us\":%llu,\"dx\":%.5f,\"dy\":%.5f,\"ux\":%.5f,\"uy\":%.5f", (unsigned long long)(((uint64_t)hi << 32) | lo), wl_fixed_to_double(x), wl_fixed_to_double(y), wl_fixed_to_double(ux), wl_fixed_to_double(uy)); }
static const struct zwp_relative_pointer_v1_listener relative_listener = { .relative_motion=relative_motion };
static void capabilities(void *d, struct wl_seat *s, uint32_t caps) {
    (void)d;
    if (!(caps & WL_SEAT_CAPABILITY_POINTER)) { if (pointer) fail("Pointer capability removed"); return; }
    if (!pointer) { pointer = wl_seat_get_pointer(s); wl_pointer_add_listener(pointer, &pointer_listener, NULL); relative = zwp_relative_pointer_manager_v1_get_relative_pointer(relative_manager, pointer); zwp_relative_pointer_v1_add_listener(relative, &relative_listener, NULL); }
}
static void seat_label(void *d, struct wl_seat *s, const char *name) { (void)d; (void)s; (void)name; }
static const struct wl_seat_listener seat_listener = { .capabilities=capabilities, .name=seat_label };
static void global(void *d, struct wl_registry *r, uint32_t name, const char *interface, uint32_t version) {
    (void)d;
    EVENT("protocol", ",\"interface\":\"%s\",\"advertised_version\":%u", interface, version);
    if (!strcmp(interface, "wl_compositor")) compositor=wl_registry_bind(r,name,&wl_compositor_interface,version < 4 ? version : 4);
    else if (!strcmp(interface,"wl_shm")) shm=wl_registry_bind(r,name,&wl_shm_interface,1);
    else if (!strcmp(interface,"xdg_wm_base")) wm=wl_registry_bind(r,name,&xdg_wm_base_interface,1);
    else if (!strcmp(interface,"zwp_pointer_constraints_v1")) constraints=wl_registry_bind(r,name,&zwp_pointer_constraints_v1_interface,1);
    else if (!strcmp(interface,"zwp_relative_pointer_manager_v1")) relative_manager=wl_registry_bind(r,name,&zwp_relative_pointer_manager_v1_interface,1);
    else if (!strcmp(interface,"wl_seat") && !seat) { seat_name=name; seat=wl_registry_bind(r,name,&wl_seat_interface,version < 5 ? version : 5); }
}
static void removed(void *d, struct wl_registry *r, uint32_t name) { (void)d; (void)r; if (name == seat_name) fail("Seat removed"); }
static const struct wl_registry_listener registry_listener = { .global=global, .global_remove=removed };
static void locked(void *d, struct zwp_locked_pointer_v1 *p) { (void)d; (void)p; EVENT("locked", ""); }
static void unlocked(void *d, struct zwp_locked_pointer_v1 *p) { (void)d; (void)p; EVENT("unlocked", ""); }
static const struct zwp_locked_pointer_v1_listener lock_listener = { .locked=locked, .unlocked=unlocked };
static void command(const char *line) {
    if (!strcmp(line,"quit")) stopping=1;
    else if (!strcmp(line,"unlock")) { if (lock) { zwp_locked_pointer_v1_destroy(lock); lock=NULL; wl_surface_commit(surface); } EVENT("constraint_destroyed", ""); }
    else if (!strcmp(line,"lock")) { if (!pointer || !entered || lock) { fail("Lock requires an entered pointer and no existing constraint"); return; } lock=zwp_pointer_constraints_v1_lock_pointer(constraints,surface,pointer,NULL,ZWP_POINTER_CONSTRAINTS_V1_LIFETIME_PERSISTENT); zwp_locked_pointer_v1_add_listener(lock,&lock_listener,NULL); wl_surface_commit(surface); EVENT("lock_requested", ""); }
    else fail("Unknown command");
}
int main(int argc, char **argv) {
    if (argc != 2 || strlen(argv[1]) > 120 || strspn(argv[1],"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.") != strlen(argv[1])) return 2;
    signal(SIGALRM, stop_signal); alarm(60); signal(SIGTERM, stop_signal); signal(SIGINT, stop_signal); signal(SIGPIPE, SIG_IGN);
    display=wl_display_connect(NULL); if (!display) return 2;
    struct wl_registry *registry=wl_display_get_registry(display);
    wl_registry_add_listener(registry,&registry_listener,NULL);
    if (wl_display_roundtrip(display) < 0 || !compositor || !shm || !wm || !seat || !constraints || !relative_manager) { fail("Required Wayland globals unavailable"); goto cleanup; }
    EVENT("bound_protocols", ",\"wl_seat\":%u,\"xdg_wm_base\":1,\"pointer_constraints\":1,\"relative_pointer\":1", wl_seat_get_version(seat));
    wl_seat_add_listener(seat,&seat_listener,NULL); xdg_wm_base_add_listener(wm,&wm_listener,NULL);
    surface=wl_compositor_create_surface(compositor); shell_surface=xdg_wm_base_get_xdg_surface(wm,surface); xdg_surface_add_listener(shell_surface,&surface_listener,NULL);
    top=xdg_surface_get_toplevel(shell_surface); xdg_toplevel_add_listener(top,&top_listener,NULL); xdg_toplevel_set_app_id(top,argv[1]); xdg_toplevel_set_title(top,argv[1]); xdg_toplevel_set_fullscreen(top,NULL); wl_surface_commit(surface);
    int flags=fcntl(STDIN_FILENO,F_GETFL); if (flags < 0 || fcntl(STDIN_FILENO,F_SETFL,flags | O_NONBLOCK) < 0) { fail("Cannot set nonblocking stdin"); goto cleanup; }
    double deadline=stamp()+60; char input[128]; size_t used=0;
    while (!stopping && stamp() < deadline) {
        while (wl_display_prepare_read(display) != 0) { if (wl_display_dispatch_pending(display) < 0) { failed=1; stopping=1; break; } }
        if (stopping) break;
        int flushed=wl_display_flush(display);
        if (flushed < 0 && errno != EAGAIN) { wl_display_cancel_read(display); failed=1; break; }
        struct pollfd fds[2]={{wl_display_get_fd(display),POLLIN | (flushed < 0 ? POLLOUT : 0),0},{STDIN_FILENO,POLLIN,0}};
        int result=poll(fds,2,100);
        if (result < 0) { wl_display_cancel_read(display); if (errno == EINTR) continue; failed=1; break; }
        if (fds[0].revents & POLLIN) { if (wl_display_read_events(display) < 0) { failed=1; break; } } else wl_display_cancel_read(display);
        if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) { failed=1; break; }
        if (wl_display_dispatch_pending(display) < 0) { failed=1; break; }
        if (fds[1].revents & (POLLIN | POLLHUP)) {
            char chunk[128]; ssize_t count=read(STDIN_FILENO,chunk,sizeof chunk);
            if (count == 0) break;
            if (count < 0 && errno != EAGAIN && errno != EINTR) { failed=1; break; }
            for (ssize_t i=0;i<count;++i) { if (chunk[i]=='\n') { input[used]=0; command(input); used=0; } else if (used+1 < sizeof input) input[used++]=chunk[i]; else { fail("Command too long"); break; } }
        }
    }
cleanup:
    if (lock) zwp_locked_pointer_v1_destroy(lock);
    if (relative) zwp_relative_pointer_v1_destroy(relative);
    if (pointer) { if (wl_pointer_get_version(pointer) >= 3) wl_pointer_release(pointer); else wl_pointer_destroy(pointer); }
    if (top) xdg_toplevel_destroy(top);
    if (shell_surface) xdg_surface_destroy(shell_surface);
    if (surface) wl_surface_destroy(surface);
    if (buffer) wl_buffer_destroy(buffer);
    if (pixels) munmap(pixels,pixel_bytes);
    if (seat) { if (wl_seat_get_version(seat) >= 5) wl_seat_release(seat); else wl_seat_destroy(seat); }
    if (relative_manager) zwp_relative_pointer_manager_v1_destroy(relative_manager);
    if (constraints) zwp_pointer_constraints_v1_destroy(constraints);
    if (wm) xdg_wm_base_destroy(wm);
    if (shm) wl_shm_destroy(shm);
    if (compositor) wl_compositor_destroy(compositor);
    wl_registry_destroy(registry); wl_display_flush(display); wl_display_disconnect(display);
    EVENT("disposed", ""); return failed ? 1 : 0;
}
