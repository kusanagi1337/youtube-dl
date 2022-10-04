@echo off

rem Sync with the list in run_tests.sh
for /f "delims=; usebackq" %%t in (`findstr /B "DOWNLOAD_TESTS=" "%%~dpn0.sh"`) (
    set "%DOWNLOAD_TESTS%
)
IF %ERRORLEVEL% NEQ 0 (
    set "DOWNLOAD_TESTS=age_restriction|download|iqiyi_sdk_interpreter|socks|subtitles|write_annotations|youtube_lists|youtube_signature"
)

if [%%1]==[--offline-test] (
    set YTDL_TEST_SET=core
    shift
)

set test_set=""
for /f delims=^| %%t in ("%DOWNLOAD_TESTS%") (
    if "%test_set" != "" set test_set="%test_set% or "
    set test_set="%test_set%test_%%t"
)
if "%YTDL_TEST_SET%" == "core" (
    set "test_set=-k \"not (%DOWNLOAD_TESTS%)\""
) else if "%YTDL_TEST_SET%" == "download" (
    set "test_set=-k \"%DOWNLOAD_TESTS%\""
) else (
    set test_set=""
)

pytest test %test_set% %%*
