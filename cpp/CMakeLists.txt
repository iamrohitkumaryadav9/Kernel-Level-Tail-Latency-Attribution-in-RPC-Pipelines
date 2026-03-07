cmake_minimum_required(VERSION 3.22)
project(hft-cpp-components VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ── Find dependencies ─────────────────────────────────────
find_package(PkgConfig REQUIRED)
find_package(Protobuf REQUIRED)
find_package(GTest QUIET)

pkg_check_modules(GRPC REQUIRED grpc++)

find_program(GRPC_CPP_PLUGIN grpc_cpp_plugin REQUIRED)

# ── Proto codegen ─────────────────────────────────────────
set(PROTO_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../proto/order.proto")
set(PROTO_OUT "${CMAKE_CURRENT_BINARY_DIR}/proto")
file(MAKE_DIRECTORY ${PROTO_OUT})

# Generate C++ protobuf + gRPC stubs
add_custom_command(
    OUTPUT
        "${PROTO_OUT}/order.pb.cc"
        "${PROTO_OUT}/order.pb.h"
        "${PROTO_OUT}/order.grpc.pb.cc"
        "${PROTO_OUT}/order.grpc.pb.h"
    COMMAND protobuf::protoc
        --cpp_out=${PROTO_OUT}
        --grpc_out=${PROTO_OUT}
        --plugin=protoc-gen-grpc=${GRPC_CPP_PLUGIN}
        -I "${CMAKE_CURRENT_SOURCE_DIR}/../proto"
        "${PROTO_SRC}"
    DEPENDS "${PROTO_SRC}"
    COMMENT "Generating C++ protobuf/gRPC stubs from order.proto"
)

# Proto library (shared by all components)
add_library(order_proto STATIC
    "${PROTO_OUT}/order.pb.cc"
    "${PROTO_OUT}/order.grpc.pb.cc"
)
target_include_directories(order_proto PUBLIC ${PROTO_OUT} ${Protobuf_INCLUDE_DIRS})
target_link_libraries(order_proto PUBLIC
    ${Protobuf_LIBRARIES}
    ${GRPC_LIBRARIES}
)
target_compile_options(order_proto PUBLIC ${GRPC_CFLAGS_OTHER})

# ── Subdirectories ────────────────────────────────────────
add_subdirectory(loadgen)
add_subdirectory(analyzer)
add_subdirectory(matching_engine)

if(GTest_FOUND)
    add_subdirectory(tests)
else()
    message(STATUS "GTest not found — skipping tests")
endif()
