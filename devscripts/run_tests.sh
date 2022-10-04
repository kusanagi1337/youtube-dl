#!/bin/sh

if [ "$1" = "--offline-test" ]; then
    YTDL_TEST_SET=core
    shift
fi

DOWNLOAD_TESTS="age_restriction|download|iqiyi_sdk_interpreter|socks|subtitles|write_annotations|youtube_lists|youtube_signature"

test_set=$(echo "$DOWNLOAD_TESTS" | sed -re 's/^|[|]/&test_/g;s/[|]/ or /g')

case "$YTDL_TEST_SET" in
    core)
        test_set=$(printf 'not (%s)' "$test_set")
    ;;
    download)
    ;;
    *)
        test_set=
    ;;
esac

pwd
# shellcheck disable=SC2086
${PYTHON:-} pytest test/ ${test_set:+-k} "$test_set" "$@"


