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
  security: reviewed   The owner commented "reviewed <fingerprint>" for the current text.
                       Set by this check; any change to the text gives a new fingerprint.
  needs clarification  A feature request or bug report is missing required sections.

Text may contain only plain characters (ASCII, Latin-1, Latin Extended-A, common
punctuation, arrows, box drawing and emoji); anything else is flagged. After
%d flagged runs on one issue, or when a tripwire code is found, the flag is held
until the owner reviews it, and the owner is assigned.
""" % 3
import hashlib
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
MAX_COMMENTS = 1000
HOLD_AFTER = 3
# SHA-256 of tripwire codes. They only appear in text meant for automated readers, so
# finding one means a tool generated or probed the issue. The codes stay unpublished here.
TRIPWIRES = {
    "dfc526e0eaaecf4ed7e350fc56ee3fc885c9930df630dd462baf7138747d33dc",
    "7d65aa37c7a6ccab559239be1d59f52e97443ff3457d98c443b0ce6ae7b78c28",
}

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
    "characters": "Characters outside plain text (other scripts, look-alike letters, combining marks, unusual spaces or symbols)",
    "authority": "Claims of maintainer approval or of a policy change",
    "paths": "Local configuration or credential paths (.git/config, .ssh, home folder dotfiles, /etc, /root)",
    "review": "Text that needs maintainer review",
    "encoded": "Long encoded data (base64 or hex)",
    "length": "More than %d characters" % MAX_LENGTH,
    "volume": "More than %d comments, which is more than this check reads" % MAX_COMMENTS,
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
ALLOWED_RANGES = [
    (0x0009, 0x000A), (0x000D, 0x000D), (0x0020, 0x007E),
    (0x00A0, 0x00AC), (0x00AE, 0x00FF),          # Latin-1 without the soft hyphen
    (0x0100, 0x0130), (0x0132, 0x017E),          # Latin Extended-A without dotless i and long s
    (0x2010, 0x2015), (0x2018, 0x201F), (0x2022, 0x2022), (0x2026, 0x2026), (0x2030, 0x2030),
    (0x2032, 0x2033), (0x20AC, 0x20AC), (0x2122, 0x2122), (0x2190, 0x21FF), (0x2212, 0x2212),
    (0x2264, 0x2265), (0x2500, 0x257F),          # punctuation, euro, trademark, arrows, math, box drawing
    (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0x1F000, 0x1FAFF),  # emoji and symbols
    (0xFE0F, 0xFE0F), (0x20E3, 0x20E3),          # emoji presentation and keycap
]


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


def unusual_characters(text):
    """True for any character outside ALLOWED_RANGES, except a zero-width joiner inside an emoji sequence."""
    for index, char in enumerate(text):
        code = ord(char)
        if code == ZERO_WIDTH_JOINER and 0 < index < len(text) - 1 and is_emoji(text[index - 1]) and is_emoji(text[index + 1]):
            continue
        if not in_ranges(code, ALLOWED_RANGES):
            return True
    return False


def normalize(text):
    """NFKC (styled and fullwidth letters become plain), combining marks and hidden characters removed,
    lowercased, spaces collapsed."""
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text))
    text = "".join(c for c in text if ord(c) in (0x09, 0x0A, 0x0D) or not (
        unicodedata.category(c) in ("Mn", "Me") or in_ranges(ord(c), HIDDEN_RANGES) or ord(c) == ZERO_WIDTH_JOINER))
    text = unicodedata.normalize("NFC", text).replace("\u3002", ".").replace("\uff61", ".")
    return re.sub(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+", " ", text.casefold())


def tripwire(text):
    """True when a token of the normalized text is a tripwire code."""
    return any(hashlib.sha256(token.encode("utf-8")).hexdigest() in TRIPWIRES
               for token in re.findall(r"[a-z0-9][a-z0-9\-]{5,40}", normalize(text)))


def strip_code(text):
    """Text outside fenced and inline code, which GitHub does not render as HTML or math."""
    text = re.sub(r"(?ms)^[ \t]*(```|~~~).*?(^[ \t]*\1[ \t]*$|\Z)", " ", text)
    return re.sub(r"`[^`\n]*`", " ", text)


def rx(pattern, flags=0):
    return re.compile(pattern, flags)


VERBS_OVERRIDE = (r"ignore|disregard|forget|override|bypass|discard|abandon|neglect|set aside|put aside|pay no attention to|"
                  r"supersede|disobey|stop following|no longer follow")
NOUNS_OVERRIDE = (r"instructions?|prompts?|rules|directions|guidelines|guidance|context|constraints|polic(?:y|ies)|"
                  r"programming|directives?|guardrails?|safeguards?|restrictions|system message|briefing|the above|"
                  r"everything above|what you were told|earlier text|task description")
SECRET_NOUNS = (r"tokens?|secrets?|passwords?|passphrases?|api ?keys?|access keys?|private keys?|ssh keys?|credentials?|"
                r"cookies?|env(?:ironment)? ?var(?:iable)?s?|\.env\b|github_token|source_repo_token|vpm_dispatch_token|"
                r"signing keys?|deploy keys?|pats?\b|auth(?:entication)? (?:headers?|tokens?)|remote urls?|"
                r"config(?:uration)? (?:values|file contents)")
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
        rx(r"\[[0-9a-f:]*::?[0-9a-f:]*\]|\b[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){7}\b|\b[0-9a-f]{1,4}::[0-9a-f]{1,4}\b"),
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
           r"(?:instructions?|prompts|directives?|system prompt)\b"),
        rx(r"\b(?:ignora|ignorez|ignoriere|ignorieren|ignoriruy|instrucciones|anweisungen|instruktsii|consignes|instrucoes|istruzioni)\b"),
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
        rx(r"\bwhoever (?:is )?(?:reads?|reading|summari[sz]es|summari[sz]ing|reviews?|reviewing|processes|triages)\b|"
           r"\bautomated (?:reviewer|reader|assistant|agent|system|tool|triage)\b|\bthe reviewing (?:tool|system|agent)\b|"
           r"\bsummari[sz]er\b|\bif you are (?:an? )?(?:automated|automatic)\b"),
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
        rx(r"\brecommend (?:merging|approving|closing|removing|adding|releasing)\b|"
           r"\battach (?:the |this |another |a )?(?:repo|repository)\b|"
           r"\b(?:include|copy|paste|quote|repeat|reproduce)\b[^.\n]{0,30}\b(?:verbatim|word for word|exactly as written|in full)\b|"
           r"\bclarification (?:comment|draft|text)\b"),
    ],
    "authority": [
        rx(r"\b(?:maintainers?|owner|admins?|administrator|riry|rionquosue|repository owner|the team)\b[^.\n]{0,40}"
           r"\b(?:approved|authori[sz]ed|permits?|has allowed|signed off|pre-approved|whitelisted|allowlisted)\b"),
        rx(r"\bpolicy (?:update|change)\b|\bupdated (?:repository |repo )?polic(?:y|ies)\b|"
           r"\bper (?:the )?(?:new |updated )?(?:repository |repo |project )?polic(?:y|ies)\b"),
    ],
    "paths": [
        rx(r"~/\.|/root\b|/etc/|\.git/config|\.gitconfig|\.ssh\b|\.config/|id_rsa|id_ed25519|\.npmrc|\.netrc|"
           r"\.aws/|/proc/|\.claude\b|\.ccr\b|\.git-credentials"),
    ],
    "review": [
        # Names of this check's own labels: ordinary reports have no reason to mention them.
        rx(r"issue-check|security: ?(?:flagged|reviewed)|issue_check"),
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
DOMAIN = rx(r"\b(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(%s)\b" % TLDS, re.IGNORECASE)
UPPER_TLDS = {"com", "net", "org", "dev", "app", "xyz", "ru", "cn", "info", "site", "online", "top", "link", "click"}


def has_domain(text):
    """A domain with a listed TLD, ignoring all-caps endings like "System.IO" that read as code."""
    for match in DOMAIN.finditer(text):
        tld = match.group(1)
        if not tld.isupper() or tld.lower() in UPPER_TLDS:
            return True
    return False
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
    if has_domain(nfkc.replace("\u3002", ".").replace("\uff61", ".")):
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
    if unusual_characters(text):
        found.add("characters")
    if tripwire(text):
        found.add("review")
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


def report(findings, missing, kind, fingerprint, strikes, held):
    """The bot comment for these results, or None when everything passes and there is no state to keep."""
    if not findings and not missing and not strikes and not held:
        return None
    lines = [MARKER, "<!-- state strikes=%d held=%d -->" % (strikes, int(held)), "**Automated issue check**", ""]
    if held:
        lines += ["This issue is held for maintainer review. Edits will not clear it.", ""]
    elif findings:
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
    if not findings and not missing:
        lines += ["All checks pass.", ""]
    if findings:
        lines.append("Owner: to clear this after reading the issue and every comment, comment `reviewed %s`. "
                     "Any later change to the text needs a new review." % fingerprint)
    lines.append("This check runs again when the issue or a comment changes.")
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
        """All comments, and whether that is all of them (False past MAX_COMMENTS)."""
        result = []
        for page in range(1, MAX_COMMENTS // 100 + 1):
            batch = self.call("GET", "/issues/%d/comments?per_page=100&page=%d" % (number, page))
            result += batch
            if len(batch) < 100:
                return result, True
        return result, False

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


def is_own_report(comment):
    return (comment.get("user") or {}).get("login") == "github-actions[bot]" and MARKER in (comment.get("body") or "")


def is_maintainer(item, owner):
    """Owner, members and collaborators. Bots other than this check are treated as outsiders."""
    return item.get("author_association") in MAINTAINERS or (item.get("user") or {}).get("login") == owner


def issue_text(issue):
    # The form's "[Feature]: " or "[Bug]: " title prefix reads like a Markdown link reference.
    title = re.sub(r"^\s*\[(?:feature|bug)\]:\s*", "", issue.get("title") or "", flags=re.IGNORECASE)
    return title + "\n" + (issue.get("body") or "")


def content_fingerprint(issue, outsider_comments):
    """First 12 hex digits of a SHA-256 over the title, body and every outsider comment."""
    digest = hashlib.sha256()
    for part in [issue.get("title") or "", issue.get("body") or ""] + [
            "%s\0%s" % (c["id"], c.get("body") or "") for c in outsider_comments]:
        digest.update(part.encode("utf-8") + b"\0\0")
    return digest.hexdigest()[:12]


def check_texts(issue, outsider_comments, complete, owner):
    """Findings for an issue: (where, check names) pairs, including the conversation read as one text."""
    findings = []
    texts = []
    if not is_maintainer(issue, owner):
        texts.append(issue_text(issue))
        names = scan(texts[-1])
        if names:
            findings.append(("The issue", names))
    for comment in outsider_comments:
        texts.append(comment.get("body") or "")
        names = scan(texts[-1])
        if names:
            findings.append(("Comment from %s (%s)" % (comment["user"]["login"], comment["html_url"]), names))
    # Text split across lines or comments is read together by the triage agent.
    seen = {name for _, names in findings for name in names}
    joined = [n for n in scan(" ".join(re.sub(r"\s+", " ", t) for t in texts)) if n not in seen and n != "length"]
    if joined:
        findings.append(("The conversation as a whole", joined))
    if not complete:
        findings.append(("The comments", ["volume"]))
    return findings


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

    # Fail closed before anything else: triage reads nothing unless this run finishes and passes.
    github.call("DELETE", "/issues/%d/labels/%s" % (number, urllib.parse.quote(PASSED, safe="")), allow=(404,))
    issue = github.call("GET", "/issues/%d" % number)
    labels = {label["name"] for label in issue.get("labels", [])} - {PASSED}
    comments, complete = github.comments(number)
    own = next((c for c in comments if is_own_report(c)), None)
    outsiders = [c for c in comments if not is_own_report(c) and not is_maintainer(c, owner)]
    fingerprint = content_fingerprint(issue, outsiders)
    state = re.search(r"<!-- state strikes=(\d+) held=([01]) -->", (own or {}).get("body") or "")
    strikes, held = (int(state.group(1)), state.group(2) == "1") if state else (0, False)

    findings = check_texts(issue, outsiders, complete, owner)
    # The owner's review covers exactly the text it names; any change gives a new fingerprint.
    reviewed = any((c.get("user") or {}).get("login") == owner and
                   re.search(r"\breviewed\s+%s\b" % fingerprint, c.get("body") or "") for c in comments)
    if reviewed:
        findings, strikes, held = [], 0, False
    else:
        if findings and sender.get("login") != owner and action in ("opened", "edited", "created", "reopened"):
            strikes += 1
        if any("review" in names for _, names in findings) or strikes >= HOLD_AFTER:
            held = True
        if held:
            findings = findings or [("The issue", ["review"])]
            if owner not in [a["login"] for a in issue.get("assignees") or []]:
                github.call("POST", "/issues/%d/assignees" % number, {"assignees": [owner]}, allow=(422,))

    kind = issue_type(issue)
    missing = missing_sections(issue.get("body"), kind["required"]) if kind else []

    github.set_label(number, labels, FLAGGED, bool(findings))
    github.set_label(number, labels, REVIEWED, reviewed)
    github.set_label(number, labels, CLARIFY, bool(missing))

    body = report(findings, missing, kind, fingerprint, strikes, held)
    if body and own and own["body"] != body:
        github.call("PATCH", "/issues/comments/%d" % own["id"], {"body": body})
    elif body and not own:
        github.call("POST", "/issues/%d/comments" % number, {"body": body})
    elif not body and own:
        github.call("DELETE", "/issues/comments/%d" % own["id"], allow=(404,))
    # Last, so the issue is only marked as passed once its report is up to date.
    github.set_label(number, labels, PASSED, not findings and not missing)

    # Workflow logs are public: give the outcome only.
    print("Issue #%d: %s." % (number, "flagged" if findings else "needs clarification" if missing else "passed"))


if __name__ == "__main__":
    sys.exit(main())
