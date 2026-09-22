# -*- coding: utf-8 -*-
"""Reference (pure Python) text transforms, verbatim from docxtpl 0.20.1.

Original author: Eric Lapouyade. These functions define the behaviour the
native kernels in ``docxtpl._native`` must reproduce byte for byte. They are
used when the native path declines an input and as the oracle of the
differential tests. Do not "improve" them.
"""
import re


def patch_xml(src_xml):
    """Make a lots of cleaning to have a raw xml understandable by jinja2 :
    strip all unnecessary xml tags, manage table cell background color and colspan,
    unescape html entities, etc..."""

    # replace {<something>{ by {{   ( works with {{ }} {% and %} {# and #})
    src_xml = re.sub(
        r"(?<={)(<[^>]*>)+(?=[\{%\#])|(?<=[%\}\#])(<[^>]*>)+(?=\})",
        "",
        src_xml,
        flags=re.DOTALL,
    )

    # replace {{<some tags>jinja2 stuff<some other tags>}} by {{jinja2 stuff}}
    # same thing with {% ... %} and {# #}
    # "jinja2 stuff" could a variable, a 'if' etc... anything jinja2 will understand
    def striptags(m):
        return re.sub(
            "</w:t>.*?(<w:t>|<w:t [^>]*>)", "", m.group(0), flags=re.DOTALL
        )

    src_xml = re.sub(
        r"{%(?:(?!%}).)*|{#(?:(?!#}).)*|{{(?:(?!}}).)*",
        striptags,
        src_xml,
        flags=re.DOTALL,
    )

    # manage table cell colspan
    def colspan(m):
        cell_xml = m.group(1) + m.group(3)
        cell_xml = re.sub(
            r"<w:r[ >](?:(?!<w:r[ >]).)*<w:t></w:t>.*?</w:r>",
            "",
            cell_xml,
            flags=re.DOTALL,
        )
        cell_xml = re.sub(r"<w:gridSpan[^/]*/>", "", cell_xml, count=1)
        return re.sub(
            r"(<w:tcPr[^>]*>)",
            r'\1<w:gridSpan w:val="{{%s}}"/>' % m.group(2),
            cell_xml,
        )

    src_xml = re.sub(
        r"(<w:tc[ >](?:(?!<w:tc[ >]).)*){%\s*colspan\s+([^%]*)\s*%}(.*?</w:tc>)",
        colspan,
        src_xml,
        flags=re.DOTALL,
    )

    # manage table cell background color
    def cellbg(m):
        cell_xml = m.group(1) + m.group(3)
        cell_xml = re.sub(
            r"<w:r[ >](?:(?!<w:r[ >]).)*<w:t></w:t>.*?</w:r>",
            "",
            cell_xml,
            flags=re.DOTALL,
        )
        cell_xml = re.sub(r"<w:shd[^/]*/>", "", cell_xml, count=1)
        return re.sub(
            r"(<w:tcPr[^>]*>)",
            r'\1<w:shd w:val="clear" w:color="auto" w:fill="{{%s}}"/>' % m.group(2),
            cell_xml,
        )

    src_xml = re.sub(
        r"(<w:tc[ >](?:(?!<w:tc[ >]).)*){%\s*cellbg\s+([^%]*)\s*%}(.*?</w:tc>)",
        cellbg,
        src_xml,
        flags=re.DOTALL,
    )

    # ensure space preservation
    src_xml = re.sub(
        r"<w:t>((?:(?!<w:t>).)*)({{.*?}}|{%.*?%})",
        r'<w:t xml:space="preserve">\1\2',
        src_xml,
        flags=re.DOTALL,
    )
    src_xml = re.sub(
        r"({{r\s.*?}}|{%r\s.*?%})",
        r'</w:t></w:r><w:r><w:t xml:space="preserve">\1</w:t></w:r><w:r><w:t xml:space="preserve">',
        src_xml,
        flags=re.DOTALL,
    )

    # {%- will merge with previous paragraph text
    src_xml = re.sub(r"</w:t>(?:(?!</w:t>).)*?{%-", "{%", src_xml, flags=re.DOTALL)
    # -%} will merge with next paragraph text
    src_xml = re.sub(
        r"-%}(?:(?!<w:t[ >]|{%|{{).)*?<w:t[^>]*?>", "%}", src_xml, flags=re.DOTALL
    )

    for y in ["tr", "tc", "p", "r"]:
        # replace into xml code the row/paragraph/run containing
        # {%y xxx %} or {{y xxx}} template tag
        # by {% xxx %} or {{ xx }} without any surrounding <w:y> tags :
        # This is mandatory to have jinja2 generating correct xml code
        pat = (
            r"<w:%(y)s[ >](?:(?!<w:%(y)s[ >]).)*({%%|{{)%(y)s ([^}%%]*(?:%%}|}})).*?</w:%(y)s>"
            % {"y": y}
        )
        src_xml = re.sub(pat, r"\1 \2", src_xml, flags=re.DOTALL)

    for y in ["tr", "tc", "p"]:
        # same thing, but for {#y xxx #} (but not where y == 'r', since that
        # makes less sense to use comments in that context
        pat = (
            r"<w:%(y)s[ >](?:(?!<w:%(y)s[ >]).)*({#)%(y)s ([^}#]*(?:#})).*?</w:%(y)s>"
            % {"y": y}
        )
        src_xml = re.sub(pat, r"\1 \2", src_xml, flags=re.DOTALL)

    # add vMerge
    # use {% vm %} to make this table cell and its copies
    # be vertically merged within a {% for %}
    def v_merge_tc(m):
        def v_merge(m1):
            return (
                '<w:vMerge w:val="{% if loop.first %}restart{% else %}continue{% endif %}"/>'
                + m1.group(1)  # Everything between ``</w:tcPr>`` and ``<w:t>``.
                + "{% if loop.first %}"
                + m1.group(2)  # Everything before ``{% vm %}``.
                + m1.group(3)  # Everything after ``{% vm %}``.
                + "{% endif %}"
                + m1.group(4)  # ``</w:t>``.
            )

        return re.sub(
            r"(</w:tcPr[ >].*?<w:t(?:.*?)>)(.*?)(?:{%\s*vm\s*%})(.*?)(</w:t>)",
            v_merge,
            m.group(),
            # Everything between ``</w:tc>`` and ``</w:tc>`` with ``{% vm %}`` inside.
            flags=re.DOTALL,
        )

    src_xml = re.sub(
        r"<w:tc[ >](?:(?!<w:tc[ >]).)*?{%\s*vm\s*%}.*?</w:tc[ >]",
        v_merge_tc,
        src_xml,
        flags=re.DOTALL,
    )

    # Use ``{% hm %}`` to make table cell become horizontally merged within
    # a ``{% for %}``.
    def h_merge_tc(m):
        xml_to_patch = (
            m.group()
        )  # Everything between ``</w:tc>`` and ``</w:tc>`` with ``{% hm %}`` inside.

        def with_gridspan(m1):
            return (
                m1.group(1)  # ``w:gridSpan w:val="``.
                + "{{ "
                + m1.group(2)
                + " * loop.length }}"  # Content of ``w:val``, multiplied by loop length.
                + m1.group(3)  # Closing quotation mark.
            )

        def without_gridspan(m2):
            return (
                '<w:gridSpan w:val="{{ loop.length }}"/>'
                + m2.group(1)  # Everything between ``</w:tcPr>`` and ``<w:t>``.
                + m2.group(2)  # Everything before ``{% hm %}``.
                + m2.group(3)  # Everything after ``{% hm %}``.
                + m2.group(4)  # ``</w:t>``.
            )

        if re.search(r"w:gridSpan", xml_to_patch):
            # Simple case, there's already ``gridSpan``, multiply its value.

            xml = re.sub(
                r'(w:gridSpan w:val=")(\d+)(")',
                with_gridspan,
                xml_to_patch,
                flags=re.DOTALL,
            )
            xml = re.sub(
                r"{%\s*hm\s*%}",
                "",
                xml,  # Patched xml.
                flags=re.DOTALL,
            )
        else:
            # There're no ``gridSpan``, add one.
            xml = re.sub(
                r"(</w:tcPr[ >].*?<w:t(?:.*?)>)(.*?)(?:{%\s*hm\s*%})(.*?)(</w:t>)",
                without_gridspan,
                xml_to_patch,
                flags=re.DOTALL,
            )

        # Discard every other cell generated in loop.
        return "{% if loop.first %}" + xml + "{% endif %}"

    src_xml = re.sub(
        r"<w:tc[ >](?:(?!<w:tc[ >]).)*?{%\s*hm\s*%}.*?</w:tc[ >]",
        h_merge_tc,
        src_xml,
        flags=re.DOTALL,
    )

    def clean_tags(m):
        return (
            m.group(0)
            .replace(r"&#8216;", "'")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
        )

    src_xml = re.sub(r"(?<=\{[\{%])(.*?)(?=[\}%]})", clean_tags, src_xml)

    return src_xml


def resolve_listing(xml):

    def resolve_text(run_properties, paragraph_properties, m):
        xml = m.group(0).replace(
            "\t",
            "</w:t></w:r>"
            "<w:r>%s<w:tab/></w:r>"
            '<w:r>%s<w:t xml:space="preserve">' % (run_properties, run_properties),
        )
        xml = xml.replace(
            "\a",
            "</w:t></w:r></w:p>"
            '<w:p>%s<w:r>%s<w:t xml:space="preserve">'
            % (paragraph_properties, run_properties),
        )
        xml = xml.replace("\n", '</w:t><w:br/><w:t xml:space="preserve">')
        xml = xml.replace(
            "\f",
            "</w:t></w:r></w:p>"
            '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            '<w:p>%s<w:r>%s<w:t xml:space="preserve">'
            % (paragraph_properties, run_properties),
        )
        return xml

    def resolve_run(paragraph_properties, m):
        run_properties = re.search(r"<w:rPr>.*?</w:rPr>", m.group(0))
        run_properties = run_properties.group(0) if run_properties else ""
        return re.sub(
            r"<w:t(?: [^>]*)?>.*?</w:t>",
            lambda x: resolve_text(run_properties, paragraph_properties, x),
            m.group(0),
            flags=re.DOTALL,
        )

    def resolve_paragraph(m):
        paragraph_properties = re.search(r"<w:pPr>.*?</w:pPr>", m.group(0))
        paragraph_properties = (
            paragraph_properties.group(0) if paragraph_properties else ""
        )
        return re.sub(
            r"<w:r(?: [^>]*)?>.*?</w:r>",
            lambda x: resolve_run(paragraph_properties, x),
            m.group(0),
            flags=re.DOTALL,
        )

    xml = re.sub(
        r"<w:p(?: [^>]*)?>.*?</w:p>", resolve_paragraph, xml, flags=re.DOTALL
    )

    return xml
