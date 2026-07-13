cmake_minimum_required(VERSION 3.22.1)

project(edge_impulse_bridge LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(edge_impulse_bridge SHARED native-lib.cpp)

set(EI_VENDOR_DIR "${CMAKE_CURRENT_SOURCE_DIR}/edge_impulse_vendor")
file(GLOB_RECURSE EI_VENDOR_SOURCES CONFIGURE_DEPENDS
    "${EI_VENDOR_DIR}/edge-impulse-sdk/*.c"
    "${EI_VENDOR_DIR}/edge-impulse-sdk/*.cc"
    "${EI_VENDOR_DIR}/edge-impulse-sdk/*.cpp"
    "${EI_VENDOR_DIR}/model-parameters/*.c"
    "${EI_VENDOR_DIR}/model-parameters/*.cc"
    "${EI_VENDOR_DIR}/model-parameters/*.cpp"
    "${EI_VENDOR_DIR}/tflite-model/*.c"
    "${EI_VENDOR_DIR}/tflite-model/*.cc"
    "${EI_VENDOR_DIR}/tflite-model/*.cpp"
)

if(EI_VENDOR_SOURCES)
    target_sources(edge_impulse_bridge PRIVATE ${EI_VENDOR_SOURCES})
endif()

target_include_directories(edge_impulse_bridge PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${EI_VENDOR_DIR}"
)

find_library(log-lib log)
target_link_libraries(edge_impulse_bridge PRIVATE ${log-lib})
