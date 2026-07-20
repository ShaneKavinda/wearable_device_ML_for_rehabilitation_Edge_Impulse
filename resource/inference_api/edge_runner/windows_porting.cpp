#include "edge-impulse-sdk/porting/ei_classifier_porting.h"

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <thread>

namespace {

const auto processStartedAt = std::chrono::steady_clock::now();

}  // namespace

EI_IMPULSE_ERROR ei_run_impulse_check_canceled() {
    return EI_IMPULSE_OK;
}

EI_IMPULSE_ERROR ei_sleep(int32_t timeMs) {
    if (timeMs > 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(timeMs));
    }
    return EI_IMPULSE_OK;
}

uint64_t ei_read_timer_us() {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - processStartedAt
        ).count()
    );
}

uint64_t ei_read_timer_ms() {
    return ei_read_timer_us() / 1000U;
}

void ei_printf(const char *format, ...) {
    // stdout is reserved for the runner's EIR1 binary response frames.
    va_list arguments;
    va_start(arguments, format);
    std::vfprintf(stderr, format, arguments);
    va_end(arguments);
}

void ei_printf_float(float value) {
    std::fprintf(stderr, "%f", static_cast<double>(value));
}

void ei_putchar(char value) {
    std::fputc(value, stderr);
}

char ei_getchar() {
    // stdin is reserved for EIQ1 binary request frames.
    return 0;
}

void *ei_malloc(size_t size) {
    return std::malloc(size);
}

void *ei_calloc(size_t itemCount, size_t size) {
    return std::calloc(itemCount, size);
}

void ei_free(void *pointer) {
    std::free(pointer);
}

void DebugLog(const char *message) {
    ei_printf("%s", message);
}
