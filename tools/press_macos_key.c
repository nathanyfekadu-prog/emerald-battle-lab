#include <ApplicationServices/ApplicationServices.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: %s KEY_CODE [HOLD_MS]\n", argv[0]);
        return 2;
    }

    errno = 0;
    char *end = NULL;
    long code = strtol(argv[1], &end, 10);
    if (errno || end == argv[1] || *end != '\0' || code < 0 || code > 127) {
        fprintf(stderr, "invalid macOS key code: %s\n", argv[1]);
        return 2;
    }

    long hold_ms = argc == 3 ? strtol(argv[2], NULL, 10) : 120;
    if (hold_ms < 1 || hold_ms > 5000) {
        fprintf(stderr, "hold duration must be between 1 and 5000 ms\n");
        return 2;
    }

    CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState);
    CGEventRef down = CGEventCreateKeyboardEvent(source, (CGKeyCode)code, true);
    CGEventRef up = CGEventCreateKeyboardEvent(source, (CGKeyCode)code, false);
    if (!source || !down || !up) {
        fprintf(stderr, "could not create keyboard event\n");
        if (down) CFRelease(down);
        if (up) CFRelease(up);
        if (source) CFRelease(source);
        return 1;
    }

    CGEventPost(kCGHIDEventTap, down);
    usleep((useconds_t)(hold_ms * 1000));
    CGEventPost(kCGHIDEventTap, up);

    CFRelease(down);
    CFRelease(up);
    CFRelease(source);
    return 0;
}
