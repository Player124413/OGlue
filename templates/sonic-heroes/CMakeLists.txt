cmake_minimum_required(VERSION 3.20)
project(SonicHeroesPC VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8 OR NOT MSVC)
    message(FATAL_ERROR "SonicHeroesPC requires Visual Studio 2022 (MSVC), Windows x64. "
                        "Configure with -G \"Visual Studio 17 2022\" -A x64.")
endif()

# Static CRT: the game archive does not require the user to install the VS
# redistributable before launching it. Windows supplies D3D11/XAudio2.8.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
add_compile_definitions(WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)

set(XBOXRECOMP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/oglue" CACHE PATH
    "OGlue runtime source included beside this project")
if(NOT EXISTS "${XBOXRECOMP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "OGlue sources are missing at ${XBOXRECOMP_DIR}.")
endif()
add_subdirectory("${XBOXRECOMP_DIR}" "${CMAKE_BINARY_DIR}/oglue")

file(GLOB RECOMP_GEN_SOURCES CONFIGURE_DEPENDS
     "${CMAKE_CURRENT_SOURCE_DIR}/src/recomp/gen/*.c")
if(NOT RECOMP_GEN_SOURCES)
    message(FATAL_ERROR "No generated C files. Run tools.ci.heroes_build prepare first.")
endif()

add_executable(SonicHeroesPC WIN32
    src/main.c
    src/recomp_manual.c
    ${RECOMP_GEN_SOURCES}
)
target_include_directories(SonicHeroesPC PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${CMAKE_CURRENT_SOURCE_DIR}/src/recomp/gen"
    "${XBOXRECOMP_DIR}/include"
)
target_link_libraries(SonicHeroesPC PRIVATE xboxrecomp
    d3d11 dxgi dxguid d3dcompiler xinput winmm dbghelp bcrypt)
target_link_options(SonicHeroesPC PRIVATE /LARGEADDRESSAWARE /INCREMENTAL:NO)

# Millions of lines of mechanical C: keep object sections small and do not
# drown the build log in conversion warnings from generated code.
set_source_files_properties(${RECOMP_GEN_SOURCES} PROPERTIES
    COMPILE_OPTIONS "/w;/O2;/bigobj")
