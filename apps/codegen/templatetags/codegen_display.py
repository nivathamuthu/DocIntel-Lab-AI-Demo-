from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.codegen.render import failing_lines

register = template.Library()


@register.filter
def python_html(code: str):
    """Syntax-highlighted Python for <pre class="code">; plain escaped text if Pygments is missing."""
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import PythonLexer
    except ImportError:
        return escape(code or "")
    return mark_safe(highlight(code or "", PythonLexer(), HtmlFormatter(noclasses=True, style="monokai", nowrap=True)))


@register.filter
def pytest_html(output: str):
    """pytest output with failing lines and passing results marked."""
    failing = failing_lines(output or "")
    lines = []
    for index, line in enumerate((output or "").splitlines()):
        if index in failing:
            lines.append(f'<span class="line-fail">{escape(line)}</span>')
        elif " PASSED" in line or (line.startswith("=") and " passed" in line):
            lines.append(f'<span class="line-pass">{escape(line)}</span>')
        else:
            lines.append(escape(line))
    return mark_safe("\n".join(lines))
