#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>

#if defined(_WIN32)
#include <fcntl.h>
#include <io.h>
#endif

#include "edge-impulse-sdk/classifier/ei_run_classifier.h"
#include "model-parameters/model_metadata.h"
#include "model-parameters/model_variables.h"

namespace {

constexpr std::array<char, 4> kRequestMagic = {'E', 'I', 'Q', '1'};
constexpr std::array<char, 4> kResponseMagic = {'E', 'I', 'R', '1'};
constexpr uint32_t kFeatureCount = EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE;

std::array<float, kFeatureCount> features{};

template <typename T>
bool readExact(T *value) {
    std::cin.read(reinterpret_cast<char *>(value), sizeof(T));
    return static_cast<bool>(std::cin);
}

template <typename T>
void writeExact(const T &value) {
    std::cout.write(reinterpret_cast<const char *>(&value), sizeof(T));
}

int getSignalData(size_t offset, size_t length, float *out) {
    if (offset + length > features.size()) {
        return -1;
    }
    std::memcpy(out, features.data() + offset, length * sizeof(float));
    return 0;
}

bool processOneRequest() {
    std::array<char, 4> magic{};
    if (!readExact(&magic)) {
        return false;
    }

    uint32_t windowId = 0;
    uint32_t featureCount = 0;
    if (!readExact(&windowId) || !readExact(&featureCount)) {
        return false;
    }

    if (magic != kRequestMagic || featureCount != kFeatureCount) {
        return false;
    }

    std::cin.read(
        reinterpret_cast<char *>(features.data()),
        static_cast<std::streamsize>(features.size() * sizeof(float))
    );
    if (!std::cin) {
        return false;
    }

    signal_t signal{};
    signal.total_length = features.size();
    signal.get_data = getSignalData;

    ei_impulse_result_t result{};
    const auto startedAt = std::chrono::steady_clock::now();
    const EI_IMPULSE_ERROR status = run_classifier(&signal, &result, false);
    const auto endedAt = std::chrono::steady_clock::now();
    const uint32_t inferenceUs = static_cast<uint32_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(endedAt - startedAt).count()
    );

    writeExact(kResponseMagic);
    writeExact(windowId);
    const int32_t responseStatus = static_cast<int32_t>(status);
    writeExact(responseStatus);
    writeExact(inferenceUs);

    for (size_t index = 0; index < EI_CLASSIFIER_LABEL_COUNT; index++) {
        const float score = status == EI_IMPULSE_OK
            ? result.classification[index].value
            : 0.0f;
        writeExact(score);
    }
    std::cout.flush();
    return static_cast<bool>(std::cout);
}

}  // namespace

int main() {
    static_assert(EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE == 198, "Unexpected model input size");
    static_assert(EI_CLASSIFIER_LABEL_COUNT == 6, "Unexpected model label count");

    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

#if defined(_WIN32)
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif

    while (processOneRequest()) {
    }
    return std::cin.eof() ? 0 : 2;
}
