#!/usr/bin/env python3
"""Checks an issue and its comments for prompt injection and required template sections.

Run by .github/workflows/issue-check.yml on issue and comment events. Issue text
is only ever matched against fixed patterns; it is never executed, fetched or
interpreted. Findings are reported by check name, without quoting the text.

Labels:
  issue-check: passed  Every check passed. Issue triage reads only issues with this
                       label. It is removed first on every run, so a failed run
                       leaves the issue unread.
  security: flagged    Text failed a security check.
  security: reviewed   Added by the owner to clear a false positive. Removed again
                       whenever someone other than the owner changes the text.
  needs clarification  A feature request or bug report is missing required sections.
"""
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
MARKER = "<!-- rirytools-issue-check -->"
PASSED = "issue-check: passed"
FLAGGED = "security: flagged"
REVIEWED = "security: reviewed"
CLARIFY = "needs clarification"
FEATURE = "feature request"
BUG = "bug"
LABEL_COLORS = {PASSED: "0e8a16", FLAGGED: "b60205", REVIEWED: "5319e7", CLARIFY: "fbca04", FEATURE: "a2eeef", BUG: "d73a4a"}
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAX_LENGTH = 8000

CHECKS = {
    "links": "Links, web addresses, IP addresses or email addresses",
    "lookup": "Requests to visit, look up, open or download something online",
    "override": "Phrases that try to override instructions (for example \"ignore previous instructions\")",
    "roleplay": "Text addressed to an AI tool, or giving it a new role",
    "delimiters": "Chat or prompt markup (system or assistant markers, special tokens)",
    "secrets": "Requests to reveal or send tokens, passwords, keys or environment variables",
    "commands": "Requests to run commands or scripts, or to merge, push, approve or release",
    "html": "Raw HTML, HTML comments or CSS (styles, fonts, imports)",
    "math": "Math (LaTeX) formatting, which can recolor, resize or hide text",
    "fonts": "Styled Unicode letters (bold, italic, fullwidth, circled, small caps) instead of plain text",
    "hidden": "Invisible, private-use or text-direction characters",
    "homoglyphs": "Words mixing Latin letters with look-alike Cyrillic or Greek letters",
    "encoded": "Long encoded data (base64 or hex)",
    "length": "More than %d characters" % MAX_LENGTH,
}

FEATURE_TEMPLATE = """### Goals
What should this let you do, and why?

### Behaviour
What do you do, and what do you see or get?

### Scope (optional)
Which tool or part of RiryTools it affects.

### Approach (optional)
How you imagine it working, if you have an idea."""

BUG_TEMPLATE = """### What happened
What went wrong, and which tool or menu you were using.

### Expected behaviour
What you expected to happen instead.

### Steps to reproduce
1. ...

### Unity version
For example 2022.3.22f1.

### RiryTools version
For example 0.7.0.

### VRChat SDK version (optional)

### Console output (optional)
Errors or warnings from the Unity Console, as text."""

# Issue types by label or title prefix. Required sections: (name, accepted headings, minimum words).
ISSUE_TYPES = [
    {"name": "feature request", "label": FEATURE, "prefix": "[feature]", "template": FEATURE_TEMPLATE, "required": [
        ("Goals", ("goals", "goal"), 4),
        ("Behaviour", ("behaviour", "behavior"), 4),
    ]},
    {"name": "bug report", "label": BUG, "prefix": "[bug]", "template": BUG_TEMPLATE, "required": [
        ("What happened", ("what happened",), 4),
        ("Expected behaviour", ("expected behaviour", "expected behavior"), 3),
        ("Steps to reproduce", ("steps to reproduce",), 4),
        ("Unity version", ("unity version",), 1),
        ("RiryTools version", ("rirytools version",), 1),
    ]},
]

# Character classes. Each entry is an inclusive (first, last) code point range.
HIDDEN_RANGES = [
    (0x0000, 0x0008), (0x000B, 0x000C), (0x000E, 0x001F), (0x007F, 0x009F),
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
    (0x3164, 0x3164), (0xE000, 0xF8FF), (0xFE00, 0xFE0D), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0),
    (0xFFF9, 0xFFFB), (0xE0000, 0xE007F), (0xE0100, 0xE01EF), (0xF0000, 0x10FFFF),
]
STYLED_RANGES = [
    (0x0250, 0x02AF),    # IPA letters used for upside-down text
    (0x1D00, 0x1D2B),    # small capitals
    (0x1D2C, 0x1D6A),    # modifier (superscript) letters
    (0x2070, 0x209F),    # superscripts and subscripts
    (0x2460, 0x24FF),    # circled letters and numbers
    (0xFF01, 0xFF5E),    # fullwidth forms
    (0x1D400, 0x1D7FF),  # mathematical bold, italic, script, fraktur, monospace
    (0x1F100, 0x1F1E5),  # enclosed and squared letters (not the flag letters after them)
]
ZERO_WIDTH_JOINER = 0x200D


def in_ranges(code, ranges):
    return any(first <= code <= last for first, last in ranges)


def is_emoji(char):
    code = ord(char)
    return code >= 0x1F000 or 0x2600 <= code <= 0x27BF


def hidden_characters(text):
    """True for any hidden character, except a zero-width joiner inside an emoji sequence."""
    for index, char in enumerate(text):
        code = ord(char)
        if code in (0x09, 0x0A, 0x0D):
            continue
        if code == ZERO_WIDTH_JOINER:
            if 0 < index < len(text) - 1 and is_emoji(text[index - 1]) and is_emoji(text[index + 1]):
                continue
            return True
        if in_ranges(code, HIDDEN_RANGES):
            return True
    return False


def normalize(text):
    """NFKC (styled and fullwidth letters become plain), hidden characters removed, lowercased, spaces collapsed."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if ord(c) in (0x09, 0x0A, 0x0D) or not (in_ranges(ord(c), HIDDEN_RANGES) or ord(c) == ZERO_WIDTH_JOINER))
    return re.sub(r"[ \t]+", " ", text.casefold())


def strip_code(text):
    """Text outside fenced and inline code, which GitHub does not render as HTML or math."""
    text = re.sub(r"(?ms)^[ \t]*(```|~~~).*?(^[ \t]*\1[ \t]*$|\Z)", " ", text)
    return re.sub(r"`[^`\n]*`", " ", text)


def rx(pattern, flags=0):
    return re.compile(pattern, flags)


VERBS_OVERRIDE = r"ignore|disregard|forget|override|bypass|discard|abandon|neglect"
NOUNS_OVERRIDE = (r"instructions?|prompts?|rules|directions|guidelines|guidance|context|constraints|polic(?:y|ies)|"
                  r"programming|directives?|guardrails?|safeguards?|restrictions|system message")
SECRET_NOUNS = (r"tokens?|secrets?|passwords?|passphrases?|api ?keys?|access keys?|private keys?|ssh keys?|credentials?|"
                r"cookies?|env(?:ironment)? ?var(?:iable)?s?|\.env\b|github_token|source_repo_token|vpm_dispatch_token")
SECRET_VERBS = (r"reveal|print|show|output|send|post|share|leak|exfiltrate|echo|dump|list|expose|tell|give|paste|upload|"
                r"display|return|include|write|copy|email|forward")
TLDS = (r"com|net|org|io|dev|app|xyz|ru|cn|ly|gg|co|ai|info|biz|site|online|top|link|click|gl|tk|cc|uk|de|jp|fr|tv|ws|"
        r"page|live|store|cloud|tech|pw|su|ml|ga|cf|gq|ir|kr|br|ca|eu|nl|au|ch|es|pl|se|fi|dk|cz|mobi|pro|edu|gov|mil|"
        r"lol|wtf|club|fun|space|website|blog|news|chat|bot|codes|zone|work|world|today|email|host|digital|network")

# Patterns matched against normalize(text) unless noted.
PATTERNS = {
    "links": [
        rx(r"\b[a-z][a-z0-9+.\-]{1,15}://"),
        rx(r"\bwww\d{0,3}\."),
        rx(r"\b(?:mailto|javascript|vbscript):|\bdata:[a-z]+/[a-z0-9.+\-]+[;,]"),
        rx(r"\]\(\s*[^)\s]"),
        rx(r"(?m)^\s*\[[^\]\n]+\]:\s*\S"),
        rx(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        rx(r"\b[\w.+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}\b"),
        rx(r"\bhxxps?\b|\[\s*\.\s*\]|[\[(]\s*dot\s*[\])]|\s+dot\s+(?:com|net|org|io|dev|app|ru|cn)\b"),
    ],
    "lookup": [
        rx(r"\b(?:visit|browse|go to|navigate to|look up|lookup|check out|fetch|download|search|google|follow|click|open)\b"
           r"[^.\n]{0,30}\b(?:websites?|web ?sites?|web ?pages?|urls?|links?|domains?|the web|internet|online|gists?|pastebin)\b"),
        rx(r"\b(?:search|look) (?:it |this |that )?(?:up )?(?:online|on the web|on the internet|on google)\b"),
    ],
    "override": [
        rx(r"\b(?:%s)\b[^.\n]{0,40}\b(?:%s)\b" % (VERBS_OVERRIDE, NOUNS_OVERRIDE)),
        rx(r"\b(?:previous|prior|above|earlier|preceding|original|initial|all|old|real|actual|new|updated|hidden|secret)\s+"
           r"(?:instructions|prompts?|directives|system prompt)\b"),
        rx(r"\b(?:instructions|rules|guidelines)\s+(?:no longer apply|are (?:void|cancelled|canceled|obsolete|outdated|revoked))\b"),
    ],
    "roleplay": [
        rx(r"\byou are (?:now|no longer|actually|an? (?:ai|assistant|agent|language model|llm|bot|model))\b"),
        rx(r"\b(?:act|behave|respond|reply|answer|roleplay) as (?:if|an?|the)\b|\bpretend (?:to be|you|that)\b"),
        rx(r"\bfrom now on\b|\bdeveloper mode\b|\bjailbr[eo]a?k|\bdo anything now\b|\bdan mode\b|\bsystem prompt\b"),
        rx(r"\b(?:dear|hey|hello|hi|attention|note to|message to|instructions? (?:for|to))\s+(?:the\s+)?"
           r"(?:ai|llm|claude|gpt|chatgpt|copilot|gemini|assistant|agent|bot|model|triage)\b"),
        rx(r"\bif you are an? (?:ai|llm|language model|assistant|agent|bot|model)\b|\bas an? (?:ai|llm|language model)\b"),
        rx(r"\b(?:claude|chatgpt|gpt-?\d\w*|copilot|gemini|llama|llm|anthropic|openai)\b"),
    ],
    "delimiters": [
        rx(r"<\|[^|>\n]{1,40}\|>|\[/?inst\]|<</?sys>>"),
        rx(r"</?\s*(?:system|assistant|user|human|instructions?|prompt|context|tool_?(?:call|result|use)s?|"
           r"function_?(?:calls?|results?)|antml[^>]*|im_start|im_end)\s*>"),
        rx(r"(?m)^\s*(?:system|assistant|human|ai)\s*:"),
        rx(r"\b(?:begin|end|start)\s+(?:of\s+)?(?:the\s+)?(?:system|prompt|instructions?)\b"),
        rx(r"(?m)^\s*#{1,6}\s*(?:system|instructions?|response|assistant)\b"),
    ],
    "secrets": [
        rx(r"\b(?:%s)\b[^.\n]{0,60}\b(?:%s)" % (SECRET_VERBS, SECRET_NOUNS)),
        rx(r"\b(?:%s)[^.\n]{0,40}\b(?:to|into|in) (?:a |the |this |your )?(?:comment|issue|reply|response|output|log)\b" % SECRET_NOUNS),
    ],
    "commands": [
        rx(r"\b(?:run|execute|eval|exec)\b[^.\n]{0,30}\b(?:commands?|shell|bash|powershell|terminal|cmd|sh)\b"),
        rx(r"\b(?:curl|wget|invoke-webrequest|iwr|netcat|ncat|nc -[a-z]|bash -c|sh -c|base64 -d|rm -rf|chmod \+x|eval\()"),
        rx(r"\b(?:please|you (?:must|should|need to|will|shall)|now|immediately)\s+"
           r"(?:merge|approve|commit|push|deploy|publish|release|delete|close|label|comment|reply|post|run|execute)\b"),
    ],
}

# Patterns matched against the text outside code, before normalization.
FORMAT_PATTERNS = {
    "html": [
        rx(r"<!--"),
        rx(r"</?[a-zA-Z][\w\-]*(?:\s[^<>]*)?/?>"),
        rx(r"(?i)@import|@font-face|font-family|font-face|\bstyle\s*=|\burl\s*\("),
    ],
    "math": [
        rx(r"\$\$"),
        rx(r"\$[^$\n]*\\[a-zA-Z]+[^$\n]*\$"),
        rx(r"\\(?:color|textcolor|colorbox|fcolorbox|fontsize|huge|Huge|tiny|scriptsize|phantom|hphantom|vphantom|text[a-z]*)\b"),
    ],
}
# Math that uses code syntax, so it is matched before the code is stripped.
MATH_CODE = rx(r"(?m)^[ \t]*(?:```|~~~)\s*math\b|\$`")
DOMAIN = rx(r"\b(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:%s)\b" % TLDS)
UPPER_DOMAIN = rx(r"\b[A-Za-z0-9\-]+\.(?:COM|NET|ORG|DEV|APP|XYZ|RU|CN|INFO|SITE|ONLINE|TOP|LINK|CLICK)\b")
BASE64 = rx(r"[A-Za-z0-9+/]{80,}={0,2}")
HEX = rx(r"\b[0-9a-fA-F]{100,}\b")
LATIN = rx(r"[A-Za-z]")
LOOKALIKE = rx(r"[\u0370-\u03FF\u0400-\u04FF]")


def scan(text):
    """Names of the CHECKS that text fails, in CHECKS order."""
    text = text or ""
    found = set()
    plain = normalize(text)
    nfkc = unicodedata.normalize("NFKC", text)
    for name, patterns in PATTERNS.items():
        if any(p.search(plain) for p in patterns):
            found.add(name)
    if DOMAIN.search(nfkc) or UPPER_DOMAIN.search(nfkc):
        found.add("links")
    outside = strip_code(text)
    for name, patterns in FORMAT_PATTERNS.items():
        if any(p.search(outside) for p in patterns):
            found.add(name)
    if MATH_CODE.search(text):
        found.add("math")
    if any(in_ranges(ord(c), STYLED_RANGES) for c in text):
        found.add("fonts")
    if hidden_characters(text):
        found.add("hidden")
    if any(LATIN.search(word) and LOOKALIKE.search(word) for word in re.findall(r"\w+", text)):
        found.add("homoglyphs")
    if BASE64.search(text) or HEX.search(text):
        found.add("encoded")
    if len(text) > MAX_LENGTH:
        found.add("length")
    return [name for name in CHECKS if name in found]


def sections(body):
    """Issue form sections: lowercased "### Heading" to its text."""
    result = {}
    current = None
    for line in (body or "").splitlines():
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            current = re.sub(r"\s*\(optional\)$", "", heading.group(1).strip().lower())
            result[current] = ""
        elif current is not None:
            result[current] += line + "\n"
    return result


def missing_sections(body, required):
    """Required sections that are missing or too short to define anything."""
    found = sections(body)
    missing = []
    for label, keys, min_words in required:
        text = next((found[k] for k in keys if k in found), "").strip()
        if text.lower() == "_no response_" or len(re.findall(r"\w+", text)) < min_words:
            missing.append(label)
    return missing


def issue_type(issue):
    """The ISSUE_TYPES entry for the issue's label or title prefix, or None."""
    names = {label["name"].lower() for label in issue.get("labels", [])}
    title = (issue.get("title") or "").lower()
    return next((t for t in ISSUE_TYPES if t["label"] in names or title.startswith(t["prefix"])), None)


def report(findings, missing, kind=None):
    """The bot comment for these results, or None when everything passes."""
    if not findings and not missing:
        return None
    lines = [MARKER, "**Automated issue check**", ""]
    if findings:
        lines += ["This issue is labelled `%s` and will not be reviewed until the text below is fixed. "
                  "Please edit it to plain text only: no links, images, HTML, special fonts, or instructions aimed at AI tools." % FLAGGED, ""]
        for where, names in findings:
            lines.append("- %s:" % where)
            lines += ["  - %s" % CHECKS[name] for name in names]
        lines.append("")
    if missing:
        needed = ", ".join(missing[:-1]) + " and " + missing[-1] if len(missing) > 1 else missing[0]
        lines += ["This %s needs %s before it can be reviewed. Details beyond that are not required. "
                  "Please edit the issue so it includes:" % (kind["name"], needed), "", "```markdown", kind["template"], "```", ""]
    lines.append("This check runs again when the issue or a comment is edited.")
    return "\n".join(lines)


class GitHub:
    def __init__(self, token, repository):
        self.token = token
        self.base = "%s/repos/%s" % (API, repository)

    def call(self, method, path, body=None, allow=()):
        request = urllib.request.Request(self.base + path, method=method,
                                         data=None if body is None else json.dumps(body).encode("utf-8"))
        request.add_header("Authorization", "Bearer " + self.token)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            if error.code in allow:
                return None
            raise

    def comments(self, number):
        result = []
        for page in range(1, 11):
            batch = self.call("GET", "/issues/%d/comments?per_page=100&page=%d" % (number, page))
            result += batch
            if len(batch) < 100:
                break
        return result

    def ensure_label(self, name):
        if self.call("GET", "/labels/" + urllib.parse.quote(name, safe=""), allow=(404,)) is None:
            self.call("POST", "/labels", {"name": name, "color": LABEL_COLORS[name]}, allow=(422,))

    def set_label(self, number, labels, name, present):
        if present and name not in labels:
            self.ensure_label(name)
            self.call("POST", "/issues/%d/labels" % number, {"labels": [name]})
        elif not present and name in labels:
            self.call("DELETE", "/issues/%d/labels/%s" % (number, urllib.parse.quote(name, safe="")), allow=(404,))


def is_bot(user):
    return (user or {}).get("type") == "Bot"


def main():
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    if "pull_request" in event.get("issue", {}):
        return
    github = GitHub(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    number = event["issue"]["number"]
    owner = event["repository"]["owner"]["login"]
    sender = event.get("sender") or {}
    action = event.get("action")

    issue = github.call("GET", "/issues/%d" % number)
    if issue.get("state") != "open":
        return
    labels = {label["name"] for label in issue.get("labels", [])}
    # Fail closed: the issue stays unread by triage unless this run finishes and everything passes.
    github.set_label(number, labels, PASSED, False)
    labels.discard(PASSED)
    comments = github.comments(number)

    # A text change by anyone but the owner voids an earlier review.
    if sender.get("login") != owner and not is_bot(sender) and action in ("opened", "edited", "created", "reopened"):
        github.set_label(number, labels, REVIEWED, False)
        labels.discard(REVIEWED)

    findings = []
    if REVIEWED not in labels:
        if issue.get("author_association") not in MAINTAINERS and issue["user"]["login"] != owner:
            # The form's "[Feature]: " or "[Bug]: " title prefix reads like a Markdown link reference.
            title = re.sub(r"^\s*\[(?:feature|bug)\]:\s*", "", issue.get("title") or "", flags=re.IGNORECASE)
            names = scan(title + "\n" + (issue.get("body") or ""))
            if names:
                findings.append(("The issue", names))
        for comment in comments:
            if is_bot(comment.get("user")) or comment.get("author_association") in MAINTAINERS or comment["user"]["login"] == owner:
                continue
            names = scan(comment.get("body"))
            if names:
                findings.append(("Comment from %s (%s)" % (comment["user"]["login"], comment["html_url"]), names))

    kind = issue_type(issue)
    missing = missing_sections(issue.get("body"), kind["required"]) if kind else []

    github.set_label(number, labels, FLAGGED, bool(findings))
    github.set_label(number, labels, CLARIFY, bool(missing))

    body = report(findings, missing, kind)
    existing = next((c for c in comments if is_bot(c.get("user")) and MARKER in (c.get("body") or "")), None)
    if body and existing and existing["body"] != body:
        github.call("PATCH", "/issues/comments/%d" % existing["id"], {"body": body})
    elif body and not existing:
        github.call("POST", "/issues/%d/comments" % number, {"body": body})
    elif not body and existing:
        github.call("DELETE", "/issues/comments/%d" % existing["id"], allow=(404,))
    # Last, so the issue is only marked as passed once its report is up to date.
    github.set_label(number, labels, PASSED, not findings and not missing)

    summary = ", ".join("%s: %s" % (where.split(" (")[0], ", ".join(names)) for where, names in findings) or "no security findings"
    print("Issue #%d: %s; missing sections: %s." % (number, summary, ", ".join(missing) or "none"))


if __name__ == "__main__":
    sys.exit(main())
