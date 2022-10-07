#!/bin/sh

if [ "$1" = "--offline-test" ]; then
    YTDL_TEST_SET=core
    shift
fi

DOWNLOAD_TESTS="age_restriction|download|iqiyi_sdk_interpreter|socks|subtitles|write_annotations|youtube_lists|youtube_signature"

test_set=$(echo "$DOWNLOAD_TESTS" | sed -re 's/^|[|]/&test_/g;s/[|]|$/.py&/g')

case "$YTDL_TEST_SET" in
    core)
        # exclude specified modules using --ignore-glob
        # (--ignore requires a full pathname match)
        test_set=$(IFS='|'; for T in $test_set; do printf " --ignore-glob=/*%s" "$T"; done )
    ;;
    download)
        # restrict to specified modules by specifying python_files= in .ini
        # (pytest forgot the --include-glob= option: PR needed)
        pytest_ini=$(mktemp)
        trap "rm -f $pytest_ini" EXIT
        ls -l
        echo "$test_set" | { cat pytest.ini; echo; sed -re 's/^/&python_files = /;s/[|]/ /g'; } >> "$pytest_ini"
        test_set="-c=$pytest_ini"
    ;;
    *)
        test_set=
    ;;
esac

# shellcheck disable=SC2086
${PYTHON:-} pytest $test_set "$@" test/

