@echo on

rem Sync with the list in run_tests.sh, or use fallback
for /f "delims=; usebackq" %%D in (`findstr /B "DOWNLOAD_TESTS=" "%~dpn0.sh"`) do (
    call :do_set "%%D"
    goto end_loop
)
:do_set
set %1
goto :eof
:end_loop

IF %ERRORLEVEL% NEQ 0 (
    set "DOWNLOAD_TESTS=age_restriction|download|iqiyi_sdk_interpreter|socks|subtitles|write_annotations|youtube_lists|youtube_signature"
)

if [%1]==[--offline-test] (
    set YTDL_TEST_SET=core
    shift
)

set "_test_set="
if "%YTDL_TEST_SET%" == "core" (
    for /f "delims=|" %%T in ("%DOWNLOAD_TESTS%") do (
        call :add_file_arg "%%T" "--ignore-glob=" " "
    )
) else if "%YTDL_TEST_SET%" == "download" (
    copy /y pytest.ini %TEMP%
    set "test_set=python_files ="
    for /f "delims=|" %%T in ("%DOWNLOAD_TESTS%") do (
        call :add_file_arg "%%T" ""
    )
    echo "%test_set" >> %TEMP%\pytest.ini
    set "_test_set=-c=%TEMP%\pytest_ini"
    type %TEMP%\pytest.ini
) else (
    set "_test_set= "
)

if defined PYTHON (
    set "pytest=%PYTHON% -m pytest"
) else (
    set pytest=pytest
)
%pytest% test %_test_set% %*

set ret=%ERRORLEVEL%
del /f /q %TEMP%\pytest.ini
exit /b %ret%

:add_file_arg
    if defined _test_set (
        set "_test_set=%_test_set%%2test_%1.py"
    ) else (
        set "_test_set=%2test_%1.py"
    )
goto :eof

