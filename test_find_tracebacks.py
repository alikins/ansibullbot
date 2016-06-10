#!/usr/bin/python
import logging
import sys

from boltons_parsed_exceptions import ParsedException
import colored_traceback.auto
import find_traceback
import tblib
import tb as tb_formatter
import json
from awesome_print import ap

logging.basicConfig(format="%(levelname)s - %(name)s - %(message)s", level=logging.DEBUG)
log = logging.getLogger('test_find_tracebacks')


def get_txt():
    f = open('tests/sample_tracebacks.txt', 'r')
    return f.read()

def find_tbs(txt):
    a = find_traceback.find_tracebacks(txt)
    return a
#    return '\n'.join(a)

def handle_tb(tb_txt):
    tb = tb_parsed = pe = None
    # log.debug('handle_tb: %s', tb_txt)
    try:
        pe = ParsedException.from_string(tb_txt)
    except Exception as e:
        log.exception(e)
        log.warning('Unable to parse the tb_txt string: %s', e)
        raise
#        log.warning('tb_txt string: %s', tb_txt)
    try:
        tb_parsed = tblib.Traceback.from_string(tb_txt)
    except Exception as e:
        log.exception(e)
        log.warning('tblib failed too: %s', e)

    if pe:
        log.debug('One Worked!\n\n')
        log.debug('pe=%s, type(pe)=%s', pe, type(pe))
        print pe

        print('filename: %s' % pe.source_file)
        ap(pe.to_dict())
        log.debug('see ^---')
    if tb_parsed:
        log.debug('tblib worked?')
        log.debug(tb_parsed)
        print tb_parsed
        print tb_formatter.verbose_traceback(Exception, None, tb_parsed.as_traceback())

def main(args):
    txt = get_txt()
    tb_list = find_tbs(txt)

    for tb_line_list in tb_list:
        tb_txt = '\n'.join(tb_line_list)
        log.debug('v: %s', tb_txt)
        handle_tb(tb_txt)

if __name__ == "__main__":
    sys.exit(main(sys.argv[:]))
