"""Run the standard detect-secrets CLI with one portable worker."""

import multiprocessing
import sys

from detect_secrets.main import main


if __name__ == '__main__':
    # Python 3.14 uses forkserver on Linux. A local fork avoids requiring an
    # AF_UNIX listener in restricted terminals; scanner rules are unchanged.
    if 'fork' in multiprocessing.get_all_start_methods():
        multiprocessing.set_start_method('fork')
    sys.exit(
        main(
            [
                '--cores',
                '1',
                'scan',
                '--all-files',
                '--no-verify',
                *sys.argv[1:],
            ]
        )
    )
