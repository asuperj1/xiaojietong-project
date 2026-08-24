# ============================================================
# 校捷通 C++ 数据访问层 · CMake 构建脚本
#
# 目标：
#   jt_db        Python 扩展模块（Windows: jt_db.pyd / Linux: jt_db.cpython-*.so）
#   jt_db_test   C++ 原生测试可执行文件
#
# 依赖：
#   - MySQL C API（Windows: MySQL Server 8.0 自带；Linux: libmysqlclient-dev）
#   - pybind11（pip install pybind11；或自动 FetchContent 拉取）
#   - CMake >= 3.20 + C++17 编译器
#
# Windows 构建示例：
#   cmake -B build -A x64 -DMYSQL_DIR="C:/Program Files/MySQL/MySQL Server 8.0"
#   cmake --build build --config Release
#   （产物输出到 ../../backend/app/db/native/）
# ============================================================

cmake_minimum_required(VERSION 3.20)
project(jt_db CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# MSVC 在 GBK 代码页下会把 UTF-8 中文注释误读为续行符，必须显式指定 UTF-8
if(MSVC)
    add_compile_options(/utf-8)
endif()

# ---------- Python / pybind11 ----------
find_package(Python 3.8 COMPONENTS Interpreter Development REQUIRED)

find_package(pybind11 CONFIG QUIET)
if(NOT pybind11_FOUND)
    # 尝试通过 Python 自身定位 pip 安装的 pybind11 cmake 目录
    execute_process(
        COMMAND ${Python_EXECUTABLE} -m pybind11 --cmakedir
        OUTPUT_VARIABLE _pybi_cmake_dir
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _pybi_rc
    )
    if(_pybi_rc EQUAL 0 AND EXISTS "${_pybi_cmake_dir}/pybind11Config.cmake")
        message(STATUS "从 Python 定位 pybind11 cmake 目录: ${_pybi_cmake_dir}")
        find_package(pybind11 CONFIG QUIET
                     PATHS "${_pybi_cmake_dir}" NO_DEFAULT_PATH)
    endif()
endif()
if(NOT pybind11_FOUND)
    message(STATUS "未找到 pybind11，将通过 FetchContent 拉取 v3.1.0")
    include(FetchContent)
    FetchContent_Declare(
        pybind11
        GIT_REPOSITORY https://github.com/pybind/pybind11.git
        GIT_TAG        v3.1.0
    )
    FetchContent_MakeAvailable(pybind11)
endif()

# ---------- MySQL C API ----------
set(MYSQL_DIR "" CACHE PATH "MySQL Server 安装目录（如 C:/Program Files/MySQL/MySQL Server 8.0）")

# ---- MySQL 目录自动发现（环境自适应）----
# 优先级：命令行 -DMYSQL_DIR > Windows 注册表/安装目录扫描 > 常见路径
if(NOT MYSQL_DIR)
    if(WIN32)
        # 1) 扫描 C:/Program Files/MySQL/MySQL Server *（取版本号最大者）
        file(GLOB _mysql_candidates "C:/Program Files/MySQL/MySQL Server *")
        if(_mysql_candidates)
            list(SORT _mysql_candidates ORDER DESCENDING)
            list(GET _mysql_candidates 0 MYSQL_DIR)
        endif()
        # 2) 兜底：读注册表（HKLM MySQL Server 安装路径）
        if(NOT MYSQL_DIR)
            execute_process(COMMAND reg query
                "HKLM\\SOFTWARE\\MySQL AB" /s
                COMMAND findstr /i "Location InstallLocation"
                OUTPUT_VARIABLE _mysql_reg
                ERROR_QUIET)
            if(_mysql_reg MATCHES "([A-Za-z]:[\\\\/][^\r\n]+)")
                string(STRIP "${CMAKE_MATCH_1}" MYSQL_DIR)
            endif()
        endif()
    elseif(EXISTS "/usr/include/mysql/mysql.h")
        set(MYSQL_DIR "/usr")
    endif()
endif()

find_path(MYSQL_INCLUDE_DIR mysql.h
          PATHS ${MYSQL_DIR}/include /usr/include/mysql)
if(WIN32)
    # Windows：优先动态导入库 libmysql.lib（运行时仅依赖 libmysql.dll）
    find_library(MYSQL_LIBRARY
                 NAMES libmysql mysqlclient
                 PATHS ${MYSQL_DIR}/lib ${MYSQL_DIR}/lib64)
else()
    find_library(MYSQL_LIBRARY
                 NAMES mysqlclient
                 PATHS ${MYSQL_DIR}/lib ${MYSQL_DIR}/lib64
                       /usr/lib/x86_64-linux-gnu)
endif()

if(NOT MYSQL_INCLUDE_DIR OR NOT MYSQL_LIBRARY)
    message(FATAL_ERROR
        "未找到 MySQL C API (mysql.h / libmysql)。\n"
        "Windows: 请安装 MySQL Server 8.0，或用 -DMYSQL_DIR=... 指定安装目录\n"
        "Linux:   sudo apt install libmysqlclient-dev\n"
        "当前 MYSQL_DIR=${MYSQL_DIR}")
endif()
message(STATUS "MySQL include: ${MYSQL_INCLUDE_DIR}")
message(STATUS "MySQL lib:     ${MYSQL_LIBRARY}")

# ---------- 核心静态库 ----------
add_library(jt_db_core STATIC
    src/mysql_connection.cpp
    src/connection_pool.cpp
    src/transaction.cpp
    src/db_session.cpp
    src/dao/user_dao.cpp
    src/dao/library_dao.cpp
    src/dao/forum_dao.cpp
    src/dao/secondhand_dao.cpp
    src/dao/job_dao.cpp
    src/dao/life_dao.cpp
)
target_include_directories(jt_db_core PUBLIC include ${MYSQL_INCLUDE_DIR})
target_link_libraries(jt_db_core PUBLIC ${MYSQL_LIBRARY})
target_compile_definitions(jt_db_core PRIVATE MYSQL_DEPRECATED_NO_WARNINGS)
if(WIN32)
    target_link_libraries(jt_db_core PUBLIC ws2_32)
endif()

# ---------- Python 扩展模块 ----------
add_library(jt_db MODULE
    pybind/pybind_wrapper.cpp
)
target_link_libraries(jt_db PRIVATE jt_db_core pybind11::module)

if(WIN32)
    set_target_properties(jt_db PROPERTIES PREFIX "" SUFFIX ".pyd")
else()
    set_target_properties(jt_db PROPERTIES PREFIX "")
endif()

# 统一输出到 backend/app/db/native（后端桥接层默认加载位置）
set(_NATIVE_DIR "${CMAKE_SOURCE_DIR}/../../backend/app/db/native")
set_target_properties(jt_db PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${_NATIVE_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${_NATIVE_DIR}"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/lib"
)

# VS 多配置生成器会把产物放到 <OUTPUT>/<Config>/ 子目录；
# 这里统一把 .pyd 与运行时依赖 DLL 拷贝到 native/ 根目录（Python 加载位置）
if(WIN32)
    get_filename_component(_mysql_dll_dir "${MYSQL_LIBRARY}" DIRECTORY)
    set(_mysql_dll "${_mysql_dll_dir}/libmysql.dll")
endif()

add_custom_command(TARGET jt_db POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:jt_db>" "${_NATIVE_DIR}/$<TARGET_FILE_NAME:jt_db>")
if(WIN32 AND EXISTS "${_mysql_dll}")
    # jt_db 运行目录（backend/app/db/native）拷贝 libmysql.dll
    add_custom_command(TARGET jt_db POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${_mysql_dll}" "${_NATIVE_DIR}")
endif()

# ---------- C++ 原生测试 ----------
option(JT_DB_BUILD_TEST "构建 C++ 原生测试" ON)
if(JT_DB_BUILD_TEST)
    add_executable(jt_db_test test/main.cpp)
    target_link_libraries(jt_db_test PRIVATE jt_db_core)
    if(WIN32 AND EXISTS "${_mysql_dll}")
        # 动态链接 libmysql.dll：运行时必需，拷贝到测试 exe 目录
        add_custom_command(TARGET jt_db_test POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${_mysql_dll}" "$<TARGET_FILE_DIR:jt_db_test>")
    endif()
endif()
