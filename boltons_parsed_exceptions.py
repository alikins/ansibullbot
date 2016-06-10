import re

if str is bytes:  # py2
    text = unicode
else:  # py3
    text = str

_frame_re = re.compile(r'^File "(?P<filepath>.+)", line (?P<lineno>\d+)'
                       r', in (?P<funcname>.+)$')
_se_frame_re = re.compile(r'^File "(?P<filepath>.+)", line (?P<lineno>\d+)')


class ParsedException(object):
    """Stores a parsed traceback and exception as would be typically
    output by :func:`sys.excepthook` or
    :func:`traceback.print_exception`.
    .. note:
       Does not currently store SyntaxError details such as column.
    """
    def __init__(self, exc_type_name, exc_msg, frames=None):
        self.exc_type = exc_type_name
        self.exc_msg = exc_msg
        self.frames = list(frames or [])

    @property
    def source_file(self):
        """
        The file path of module containing the function that raised the
        exception, or None if not available.
        """
        try:
            return self.frames[-1]['filepath']
        except IndexError:
            return None

    def to_dict(self):
        "Get a copy as a JSON-serializable :class:`dict`."
        return {'exc_type': self.exc_type,
                'exc_msg': self.exc_msg,
                'frames': list(self.frames)}

    def __repr__(self):
        cn = self.__class__.__name__
        return ('%s(%r, %r, frames=%r)'
                % (cn, self.exc_type, self.exc_msg, self.frames))

    def to_string(self):
        """Formats the exception and its traceback into the standard format,
        as returned by the traceback module.
        ``ParsedException.from_string(text).to_string()`` should yield
        ``text``.
        """
        lines = [u'Traceback (most recent call last):']

        for frame in self.frames:
            lines.append(u'  File "%s", line %s, in %s' % (frame['filepath'],
                                                           frame['lineno'],
                                                           frame['funcname']))
            source_line = frame.get('source_line')
            if source_line:
                lines.append(u'    %s' % (source_line,))
        if self.exc_msg:
            lines.append(u'%s: %s' % (self.exc_type, self.exc_msg))
        else:
            lines.append(u'%s' % (self.exc_type,))
        return u'\n'.join(lines)

    @classmethod
    def from_string(cls, tb_str):
        """Parse a traceback and exception from the text *tb_str*. This text
        is expected to have been decoded, otherwise it will be
        interpreted as UTF-8.
        This method does not search a larger body of text for
        tracebacks. If the first line of the text passed does not
        match one of the known patterns, a :exc:`ValueError` will be
        raised. This method will ignore trailing text after the end of
        the first traceback.
        Args:
            tb_str (str): The traceback text (:class:`unicode` or UTF-8 bytes)
        """
        if not isinstance(tb_str, text):
            tb_str = tb_str.decode('utf-8')
        tb_lines = tb_str.lstrip().splitlines()

        # First off, handle some ignored exceptions. These can be the
        # result of exceptions raised by __del__ during garbage
        # collection

        while tb_lines:
            cl = tb_lines[-1]
            if cl.startswith('Exception ') and cl.endswith('ignored'):
                tb_lines.pop()
            else:
                break
        if tb_lines and tb_lines[0].strip() == 'Traceback (most recent call last):':
            start_line = 1
            frame_re = _frame_re
        elif len(tb_lines) > 1 and tb_lines[-2].lstrip().startswith('^'):
            # This is to handle the slight formatting difference
            # associated with SyntaxErrors, which also don't really
            # have tracebacks
            start_line = 0
            frame_re = _se_frame_re
        else:
            raise ValueError('unrecognized traceback string format')

        frames = []
        line_no = start_line
        while True:
            frame_line = tb_lines[line_no].strip()
            frame_match = frame_re.match(frame_line)
            if frame_match:
                frame_dict = frame_match.groupdict()
                try:
                    next_line = tb_lines[line_no + 1]
                except IndexError:
                    # We read what we could
                    next_line = ''
                next_line_stripped = next_line.strip()
                if (
                        frame_re.match(next_line_stripped) or
                        # The exception message will not be indented
                        # This check is to avoid overrunning on eval-like
                        # tracebacks where the last frame doesn't have source
                        # code in the traceback
                        not next_line.startswith(' ')
                ):
                    frame_dict['source_line'] = ''
                else:
                    frame_dict['source_line'] = next_line_stripped
                    line_no += 1
            else:
                break
            line_no += 1
            frames.append(frame_dict)

        try:
            exc_line = '\n'.join(tb_lines[line_no:])
            exc_type, _, exc_msg = exc_line.partition(': ')
        except Exception:
            exc_type, exc_msg = '', ''

        return cls(exc_type, exc_msg, frames)
