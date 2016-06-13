import logging
log = logging.getLogger(__name__)

import tbgrep
from awesome_print import ap

# notes: https://regex101.com/r/zS1fN5/1
#      http://boltons.readthedocs.io/en/latest/_modules/boltons/tbutils.html#ParsedException  from https://github.com/mahmoud/boltons
#

def unescape_newlines(body):
    """Given a string with escaped newlines with embedded new lines (\n) convert to real newlines.

    Used to deuglify tracebacks reported in results, so tbgrep has a better chance of finding a traceback."""
    # body = body.replace('\\r',"")
    # body = body.replace('\\n',"\n")
    # lines = body.split('\\n')
    # body = '\n'.join(lines)
    # log.debug('AFTER: %s', body)
    return body.encode('unicode_escape')


def find_tracebacks(body):
    # TODO: add a body wrapper object that will convert embedded new lines
    #       to real new lines, so tbgrep has a better chance of finding them.
    #       -or- patch tbgrep to do the same

    body = unescape_newlines(body)

    tracebacks = tbgrep.tracebacks_from_lines(body.splitlines())

    list_of_tb_line_list = [x for x in tracebacks]
    #tbs = [x.decode('string_escape') for x in tracebacks]
    tbs = list_of_tb_line_list
    ap(tbs)
    return tbs
