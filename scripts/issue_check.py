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

Formatting, links, characters and language checks trip on their own. Wording aimed at
an AI reader is scored instead: each WORDING pattern has a weight, and a text's score
combines the weights of the patterns it matches. A score of REVIEW_SCORE flags the text;
BLOCK_SCORE removes an outsider's comment, or closes and locks an outsider's issue, and
holds the issue. Text must be English in plain characters (ASCII, common symbols,
punctuation, arrows, box drawing and emoji). The owner is assigned to every flagged
issue. After %d flagged edits by the same person, or when a tripwire code is found,
the flag is held until the owner reviews it.
""" % 3
import hashlib
import html
import json
import os
import re
import sys
import time
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
MAX_JOINED = 200000
TIME_BUDGET = 300
# Wording scores (0 to 1) at which text is flagged, and at which it is removed and the issue held.
REVIEW_SCORE = 0.45
BLOCK_SCORE = 0.8
# Checks that do not apply to maintainers' own text (their links, paths and formatting are trusted);
# everything aimed at an AI reader still does, since a maintainer may relay outsider text.
MAINTAINER_EXEMPT = {"links", "lookup", "paths", "html", "math", "encoded", "length"}
# Comma-separated SHA-256 hashes of 12-character tripwire codes (lowercase letters and
# digits), from the TRIPWIRE_HASHES Actions variable so they are not in the repository.
TRIPWIRES = {h.strip().lower() for h in os.environ.get("TRIPWIRE_HASHES", "").split(",") if h.strip()}
TRIPWIRE_LENGTH = 12

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
    "characters": "Characters outside plain English text (accented letters, other scripts, look-alike letters, combining marks, unusual spaces or symbols)",
    "language": "Text that is not in English",
    "authority": "Claims of maintainer approval or of a policy change",
    "paths": "Local configuration or credential paths (.git/config, .ssh, home folder dotfiles, /etc, /root)",
    "review": "Text that needs maintainer review",
    "encoded": "Long encoded data (base64 or hex)",
    "length": "More than %d characters" % MAX_LENGTH,
    "volume": "Too much text to check (more than %d comments or %d characters in all)" % (MAX_COMMENTS, MAX_JOINED),
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
    (0x00A0, 0x00AC), (0x00AE, 0x00BF),          # Latin-1 symbols without the soft hyphen
    (0x00D7, 0x00D7), (0x00F7, 0x00F7),          # multiplication and division signs
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
    """HTML entities decoded, NFKC (styled and fullwidth letters become plain), regional-indicator
    letters read as letters, combining marks and hidden characters removed, lowercased, spaces collapsed."""
    text = html.unescape(html.unescape(text))
    text = "".join(chr(ord(c) - 0x1F1E6 + ord("a")) if 0x1F1E6 <= ord(c) <= 0x1F1FF else c for c in text)
    text = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text))
    text = "".join(c for c in text if ord(c) in (0x09, 0x0A, 0x0D) or not (
        unicodedata.category(c) in ("Mn", "Me") or in_ranges(ord(c), HIDDEN_RANGES) or ord(c) == ZERO_WIDTH_JOINER))
    text = unicodedata.normalize("NFC", text).replace("\u3002", ".").replace("\uff61", ".")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    return re.sub(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+", " ", text.casefold())


def tripwire(text):
    """True when the letters and digits of the normalized text contain a tripwire code, whatever
    spaces, dashes, line breaks or other characters were put between its characters."""
    if not TRIPWIRES:
        return False
    compact = re.sub(r"[^a-z0-9]", "", normalize(text))
    return any(hashlib.sha256(compact[i:i + TRIPWIRE_LENGTH].encode("ascii")).hexdigest() in TRIPWIRES
               for i in range(len(compact) - TRIPWIRE_LENGTH + 1))


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

# Patterns matched against normalize(text) unless noted. Any match fails the check.
PATTERNS = {
    "links": [
        rx(r"\b[a-z][a-z0-9+.\-]{1,15}://"),
        rx(r"\bwww\d{0,3}\."),
        rx(r"\b(?:mailto|javascript|vbscript):|\bdata:[a-z]+/[a-z0-9.+\-]+[;,]"),
        rx(r"\]\(\s*[^)\s]"),
        rx(r"(?m)^[ \t]*\[[^\]\n]+\]:[ \t]*\S"),
        rx(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        rx(r"\[[0-9a-f:]*::?[0-9a-f:]*\]|\b[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){7}\b|\b[0-9a-f]{1,4}::[0-9a-f]{1,4}\b"),
        rx(r"[\w.+\-]{1,64}@[a-z0-9\-]{1,63}(?:\.[a-z0-9\-]{1,63}){0,8}\.[a-z]{2,24}\b"),
        rx(r"\bhxxps?\b|\[\s*\.\s*\]|[\[(][ \t]{0,3}dot[ \t]{0,3}[\])]|[ \t]{1,5}dot[ \t]{1,5}(?:com|net|org|io|dev|app|ru|cn)\b"),
    ],
    "delimiters": [
        rx(r"<\|[^|>\n]{1,40}\|>|\[/?inst\]|<</?sys>>"),
        rx(r"</?\s*(?:system|assistant|user|human|instructions?|prompt|context|tool_?(?:call|result|use)s?|"
           r"function_?(?:calls?|results?)|antml[^>]*|im_start|im_end)\s*>"),
        rx(r"(?m)^[ \t]*(?:system|assistant|human|ai)[ \t]*:"),
        rx(r"\b(?:begin|end|start)\s+(?:of\s+)?(?:the\s+)?(?:system|prompt|instructions?)\b"),
        rx(r"(?m)^[ \t]*#{1,6}[ \t]*(?:system|instructions?|response|assistant)\b"),
    ],
    "review": [
        # Names of this check's own labels: ordinary reports have no reason to mention them.
        rx(r"issue-check|security: ?(?:flagged|reviewed)|issue_check"),
    ],
}

# Wording patterns, matched like PATTERNS, as (weight, pattern). A weight is how strongly a match
# alone reads as prompt injection: REVIEW_SCORE or more flags the text by itself, and weaker
# matches flag it only together. Weights combine as 1 - (1 - w1)(1 - w2)..., so 0.65 and 0.5
# give 0.825 (removed), and 0.3 and 0.25 give 0.475 (flagged). Each pattern counts once per text, however often it matches.
WORDING = {
    "lookup": [
        (0.3, rx(r"\b(?:visit|browse|go to|navigate to|look up|lookup|check out|fetch|download|search|google|follow|click|open)\b"
                 r"[^.\n]{0,30}\b(?:websites?|web ?sites?|web ?pages?|urls?|links?|domains?|the web|internet|online|gists?|pastebin)\b")),
        (0.2, rx(r"\b(?:search|look) (?:it |this |that )?(?:up )?(?:online|on the web|on the internet|on google)\b")),
    ],
    "override": [
        (0.65, rx(r"\b(?:%s)\b[^.\n]{0,40}\b(?:%s)\b" % (VERBS_OVERRIDE, NOUNS_OVERRIDE))),
        (0.5, rx(r"\b(?:previous|prior|above|earlier|preceding|original|initial|all|old|real|actual|new|updated|hidden|secret)\s+"
                 r"(?:instructions?|prompts|directives?|system prompt)\b")),
        (0.5, rx(r"\b(?:ignora|ignorez|ignoriere|ignorieren|ignoriruy|instrucciones|anweisungen|instruktsii|consignes|instrucoes|istruzioni)\b")),
        (0.8, rx(r"\b(?:instructions|rules|guidelines)\s+(?:no longer apply|are (?:void|cancelled|canceled|obsolete|outdated|revoked))\b")),
    ],
    "roleplay": [
        (0.45, rx(r"\byou are (?:now|no longer|actually|an? (?:ai|assistant|agent|language model|llm|bot|model))\b")),
        (0.25, rx(r"\b(?:act|behave|respond|reply|answer|roleplay) as (?:if|an?|the)\b|\bpretend (?:to be|you|that)\b")),
        (0.15, rx(r"\bfrom now on\b|\bdeveloper mode\b")),
        (0.6, rx(r"\bjailbr[eo]a?k|\bdo anything now\b|\bdan mode\b|\bsystem prompt\b")),
        (0.5, rx(r"\b(?:dear|hey|hello|hi|attention|note to|message to|instructions? (?:for|to))\s+(?:the\s+)?"
                 r"(?:ai|llm|claude|gpt|chatgpt|copilot|gemini|assistant|agent|bot|model|triage)\b")),
        (0.6, rx(r"\bif you are an? (?:ai|llm|language model|assistant|agent|bot|model)\b|\bas an? (?:ai|llm|language model)\b")),
        (0.15, rx(r"\b(?:claude|chatgpt|gpt-?\d\w*|copilot|gemini|llama|llm|anthropic|openai)\b")),
        (0.45, rx(r"\bwhoever (?:is )?(?:reads?|reading|summari[sz]es|summari[sz]ing|reviews?|reviewing|processes|triages)\b|"
                 r"\bautomated (?:reviewer|reader|assistant|agent|system|tool|triage)\b|\bthe reviewing (?:tool|system|agent)\b|"
                 r"\bsummari[sz]er\b|\bif you are (?:an? )?(?:automated|automatic)\b")),
    ],
    "secrets": [
        (0.5, rx(r"\b(?:%s)\b[^.\n]{0,60}\b(?:%s)" % (SECRET_VERBS, SECRET_NOUNS))),
        (0.5, rx(r"\b(?:%s)[^.\n]{0,40}\b(?:to|into|in) (?:a |the |this |your )?(?:comment|issue|reply|response|output|log)\b" % SECRET_NOUNS)),
    ],
    "commands": [
        (0.2, rx(r"\b(?:run|execute|eval|exec)\b[^.\n]{0,30}\b(?:commands?|shell|bash|powershell|terminal|cmd|sh)\b")),
        (0.45, rx(r"\b(?:curl|wget|invoke-webrequest|iwr|netcat|ncat|nc -[a-z]|bash -c|sh -c|base64 -d|rm -rf|chmod \+x|eval\()")),
        (0.25, rx(r"\b(?:please|you (?:must|should|need to|will|shall)|now|immediately)\s+"
                 r"(?:merge|approve|commit|push|deploy|publish|release|delete|close|label|comment|reply|post|run|execute)\b")),
        (0.45, rx(r"\brecommend (?:merging|approving|closing|removing|adding|releasing)\b|"
                 r"\battach (?:the |this |another |a )?(?:repo|repository)\b|"
                 r"\b(?:include|copy|paste|quote|repeat|reproduce)\b[^.\n]{0,30}\b(?:verbatim|word for word|exactly as written|in full)\b|"
                 r"\bclarification (?:comment|draft|text)\b")),
    ],
    "authority": [
        (0.5, rx(r"\b(?:maintainers?|owner|admins?|administrator|riry|rionquosue|repository owner|the team)\b[^.\n]{0,40}"
                 r"\b(?:approved|authori[sz]ed|permits?|has allowed|signed off|pre-approved|whitelisted|allowlisted)\b")),
        (0.45, rx(r"\bpolicy (?:update|change)\b|\bupdated (?:repository |repo )?polic(?:y|ies)\b|"
                 r"\bper (?:the )?(?:new |updated )?(?:repository |repo |project )?polic(?:y|ies)\b")),
    ],
    "paths": [
        (0.5, rx(r"~/\.|/root\b|/etc/|\.git/config|\.gitconfig|\.ssh\b|\.config/|id_rsa|id_ed25519|\.npmrc|\.netrc|"
                 r"\.aws/|/proc/|\.claude\b|\.ccr\b|\.git-credentials")),
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
DOMAIN_TOKEN = rx(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
DOMAIN_LABEL = rx(r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?")
TLD_SET = set(TLDS.split("|"))
UPPER_TLDS = {"com", "net", "org", "dev", "app", "xyz", "ru", "cn", "info", "site", "online", "top", "link", "click"}


def has_domain(text):
    """A domain with a listed TLD, ignoring all-caps endings like "System.IO" that read as code.
    Works token by token so the time stays linear in the text length."""
    for match in DOMAIN_TOKEN.finditer(text):
        labels = match.group(0).rstrip(".-").split(".")
        if len(labels) < 2 or labels[-1].lower() not in TLD_SET or not DOMAIN_LABEL.fullmatch(labels[-2]):
            continue
        if not labels[-1].isupper() or labels[-1].lower() in UPPER_TLDS:
            return True
    return False
BASE64 = rx(r"[A-Za-z0-9+/]{80,}={0,2}")
HEX = rx(r"\b[0-9a-fA-F]{100,}\b")
LATIN = rx(r"[A-Za-z]")
LOOKALIKE = rx(r"[\u0370-\u03FF\u0400-\u04FF]")


QUOTE_PREFIX = rx(r"(?m)^[ \t]*(?:(?:>|[-*+]|\d{1,3}[.)])[ \t]*)+")
# Common words of other languages that English text does not use, for text written without accents
# (accented letters already fail "characters"). Words that are also English, code or file names
# ("die", "per", "com", "ini", "mit", "jest", "todo") are left out.
FOREIGN_WORDS = set("""
los las una unos unas que por para pero muy esta este esto estos como cuando donde porque gracias hola tiene puede
hacer quiero necesito tambien nada les des une avec dans vous nous mais sur sont cette merci bonjour aussi tres peut
faire avoir und der das nicht ist ich auf ein eine wenn aber oder auch wird sind kann bitte danke haben werden uma
isso voce obrigado entao muito pra nao esse essa sono della questo questa anche perche grazie ciao che het een niet
zijn ook maar voor bedankt yang tidak itu dengan untuk saya bisa kasih ang mga naman salamat nie sie jak bir icin cok
desu arigatou kudasai onegai watashi privet spasibo pozhaluysta
""".split())
ENGLISH_WORDS = set("the an and or of to in on at is are was were be it this that for with not you my we have has but "
                    "if when what so do can will from as by".split())


def foreign(text):
    """True when the text outside code reads as another language: at least two different
    FOREIGN_WORDS, and more of them than common English words."""
    words = re.findall(r"[a-z]+", normalize(strip_code(text)))
    hits = [word for word in words if word in FOREIGN_WORDS]
    return len(set(hits)) >= 2 and len(hits) > sum(word in ENGLISH_WORDS for word in words)


def score(hits):
    """How strongly a set of (check, index) WORDING hits reads as prompt injection, from 0 to 1."""
    unlikely = 1.0
    for name, index in hits:
        unlikely *= 1 - WORDING[name][index][0]
    return 1 - unlikely


def names(found, hits):
    """CHECKS names, in CHECKS order: every check in found, and the wording checks with hits when
    their combined score reaches REVIEW_SCORE."""
    if score(hits) >= REVIEW_SCORE:
        found = found | {name for name, _ in hits}
    return [name for name in CHECKS if name in found]


def scan(text):
    """Names of the CHECKS that text fails, in CHECKS order."""
    return names(*assess(text))


def assess(text):
    """The CHECKS that text fails outright, and the (check, index) of every WORDING pattern it
    matches. Line-start checks also see each line with quote and list markers ("> ", "- ", "1. ") removed."""
    text = text or ""
    found = set()
    unquoted = QUOTE_PREFIX.sub("", text)
    # Markdown emphasis inside a word ("instruc**tions**") still renders as the plain word.
    plain = normalize(text) + "\n" + normalize(unquoted)
    plain += "\n" + re.sub(r"(?<=\w)[*_~]+|[*_~]+(?=\w)", "", plain)
    nfkc = unicodedata.normalize("NFKC", text)
    for name, patterns in PATTERNS.items():
        if any(p.search(plain) for p in patterns):
            found.add(name)
    hits = {(name, index) for name, patterns in WORDING.items()
            for index, (_, pattern) in enumerate(patterns) if pattern.search(plain)}
    if has_domain(nfkc.replace("\u3002", ".").replace("\uff61", ".")):
        found.add("links")
    outside = strip_code(text) + "\n" + strip_code(unquoted)
    for name, patterns in FORMAT_PATTERNS.items():
        if any(p.search(outside) for p in patterns):
            found.add(name)
    if MATH_CODE.search(text) or MATH_CODE.search(unquoted):
        found.add("math")
    if any(in_ranges(ord(c), STYLED_RANGES) for c in text):
        found.add("fonts")
    if hidden_characters(text):
        found.add("hidden")
    if any(LATIN.search(word) and LOOKALIKE.search(word) for word in re.findall(r"\w+", text)):
        found.add("homoglyphs")
    if unusual_characters(text):
        found.add("characters")
    if foreign(text):
        found.add("language")
    if tripwire(text):
        found.add("review")
    if BASE64.search(text) or HEX.search(text):
        found.add("encoded")
    if len(text) > MAX_LENGTH:
        found.add("length")
    return found, hits


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


def report(findings, missing, kind, fingerprint, strikes, held, reviewed=False, removed=0):
    """The bot comment for these results, or None when everything passes and there is no state to keep."""
    if not findings and not missing and not strikes and not held and not removed:
        return None
    state = ",".join("%s:%d" % item for item in sorted(strikes.items()))
    lines = [MARKER, "<!-- state strikes=%s held=%d removed=%d -->" % (state, int(held), removed), "**Automated issue check**", ""]
    if removed:
        lines += ["%d comment%s that read as prompt injection %s removed." % (removed, "s" * (removed > 1), "were" if removed > 1 else "was"), ""]
    if reviewed:
        lines += ["The owner reviewed the current text. Any change to it needs a new review.", ""]
    elif held:
        lines += ["This issue is held for maintainer review. Edits will not clear it.", ""]
    elif findings:
        lines += ["This issue is labelled `%s` and will not be reviewed until the text below is fixed. "
                  "Please edit it to plain English text only: no links, images, HTML, special fonts, accented letters, or instructions aimed at AI tools." % FLAGGED, ""]
        for where, names, _ in findings:
            lines.append("- %s:" % where)
            lines += ["  - %s" % CHECKS[name] for name in names]
        lines.append("")
    if missing:
        needed = ", ".join(missing[:-1]) + " and " + missing[-1] if len(missing) > 1 else missing[0]
        lines += ["This %s needs %s before it can be reviewed. Details beyond that are not required. "
                  "Please edit the issue so it includes:" % (kind["name"], needed), "", "```markdown", kind["template"], "```", ""]
    if not findings and not missing and not reviewed:
        lines += ["All checks pass.", ""]
    if findings:
        lines.append("Owner: to clear this after reading the issue and every comment, post a comment that is exactly "
                     "`reviewed %s`. Any later change to the text needs a new review." % fingerprint)
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


def is_own_report(comment):
    return (comment.get("user") or {}).get("login") == "github-actions[bot]" and MARKER in (comment.get("body") or "")


def is_maintainer(item):
    """Owner, members and collaborators, by GitHub's author_association. Other bots are outsiders."""
    return item.get("author_association") in MAINTAINERS


REVIEW = re.compile(r"reviewed ([0-9a-f]{64})")


def review_of(comment):
    """The fingerprint an owner review names. The whole comment must be "reviewed <64 hex digits>"."""
    if comment.get("author_association") != "OWNER":
        return None
    match = REVIEW.fullmatch((comment.get("body") or "").strip())
    return match.group(1) if match else None


def latest_review(comments):
    """The fingerprint named by the owner's most recent review comment, or None."""
    reviews = [review_of(c) for c in comments]
    return next((r for r in reversed(reviews) if r), None)


def readable_comments(comments):
    """Comments the triage agent reads: all except this check's report and owner review comments."""
    return [c for c in comments if not is_own_report(c) and review_of(c) is None]


def issue_text(issue):
    # The form's "[Feature]: " or "[Bug]: " title prefix reads like a Markdown link reference.
    title = re.sub(r"^\s*\[(?:feature|bug)\]:\s*", "", issue.get("title") or "", flags=re.IGNORECASE)
    return title + "\n" + (issue.get("body") or "")


def content_fingerprint(issue, comments):
    """SHA-256 over the issue number, title and body and every readable comment (id, author id, role, text)."""
    data = [issue.get("number"), (issue.get("user") or {}).get("id"), issue.get("title") or "", issue.get("body") or ""] + [
        [c.get("id"), (c.get("user") or {}).get("id"), c.get("author_association"), c.get("body") or ""]
        for c in readable_comments(comments)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=True).encode("ascii")).hexdigest()


def login_of(item):
    return (item.get("user") or {}).get("login") or "a deleted user"


def review_texts(issue, comments, complete):
    """Findings for an issue as (where, check names, author login) tuples, the outsider texts (the
    issue or comments) that scored BLOCK_SCORE, and whether the issue must be held.

    Every readable text is scanned; maintainers' text skips MAINTAINER_EXEMPT. One outsider's
    wording is also scored across all their texts, so splitting it over several comments does not
    lower it. The conversation read as one text is checked too, counting only wording that appears
    where texts meet, so ordinary words in different people's comments do not add up. An
    over-long conversation or a run past TIME_BUDGET fails closed."""
    findings, blocked, hold = [], [], False
    texts, seen, people = [], set(), {}
    started = time.monotonic()
    items = [("The issue", issue_text(issue), issue)] + [
        ("Comment from %s (%s)" % (login_of(c), c.get("html_url")), c.get("body") or "", c) for c in readable_comments(comments)]
    for where, text, item in items:
        if time.monotonic() - started > TIME_BUDGET:
            findings.append(("The conversation", ["volume"], None))
            return findings, blocked, hold
        # Over-long text is flagged without scanning, which keeps every run short.
        found, hits = ({"length"}, set()) if len(text) > MAX_LENGTH else assess(text)
        if is_maintainer(item):
            found -= MAINTAINER_EXEMPT
            hits = {hit for hit in hits if hit[0] not in MAINTAINER_EXEMPT}
        else:
            if score(hits) >= BLOCK_SCORE:
                blocked.append(item)
            # Deleted accounts all show as "ghost" (or no user), so each of their texts stands alone.
            user = item.get("user") or {}
            key = user.get("id") if user.get("login") not in (None, "ghost") else ("deleted", id(item))
            person = people.setdefault(key, {"login": login_of(item), "hits": set(), "flagged": False})
            person["hits"] |= hits
            person["flagged"] |= score(hits) >= REVIEW_SCORE
        texts.append(text[:MAX_LENGTH])
        seen |= hits
        flagged = names(found, hits)
        if flagged:
            findings.append((where, flagged, login_of(item)))
    for person in people.values():
        if score(person["hits"]) >= BLOCK_SCORE:
            hold = True
        if not person["flagged"] and score(person["hits"]) >= REVIEW_SCORE:
            findings.append(("Comments from %s, read together" % person["login"], names(set(), person["hits"]), person["login"]))
    # Text split across lines or comments is read together by the triage agent.
    joined = " ".join(re.sub(r"\s+", " ", t) for t in texts if t)
    if len(joined) > MAX_JOINED:
        findings.append(("The conversation as a whole", ["volume"], None))
    else:
        found, hits = assess(joined)
        hits = {hit for hit in hits if hit[0] not in MAINTAINER_EXEMPT} - seen
        if score(hits) >= BLOCK_SCORE:
            hold = True
        whole = names(found - MAINTAINER_EXEMPT, hits)
        if whole:
            findings.append(("The conversation as a whole", whole, None))
    if not complete:
        findings.append(("The comments", ["volume"], None))
    return findings, blocked, hold


def check_texts(issue, comments, complete):
    """Findings for an issue as (where, check names, author login) tuples; see review_texts."""
    return review_texts(issue, comments, complete)[0]


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
    fingerprint = content_fingerprint(issue, comments)
    state = re.search(r"<!-- state strikes=([\w\-\[\]:,]*) held=([01])(?: removed=(\d+))? -->", (own or {}).get("body") or "")
    strikes = {}
    if state:
        for entry in filter(None, state.group(1).split(",")):
            who, _, count = entry.rpartition(":")
            if who and count.isdigit():
                strikes[who] = int(count)
    held = bool(state) and state.group(2) == "1"
    removed = int(state.group(3) or 0) if state else 0

    # The owner's latest review covers exactly the text it names; any change gives a new fingerprint.
    # It is checked first, so the owner can always clear an issue, however slow it is to scan.
    reviewed = latest_review(comments) == fingerprint
    findings, blocked, hold = ([], [], False) if reviewed else review_texts(issue, comments, complete)
    if not reviewed:
        authors = {author for _, _, author in findings}
        login = sender.get("login") or ""
        # A strike counts against whoever changed flagged text, not bystanders who comment after it.
        if (findings and login != owner and action in ("opened", "edited", "created", "reopened")
                and (login in authors or None in authors)):
            strikes[login] = strikes.get(login, 0) + 1
        if (hold or blocked or any("review" in checks for _, checks, _ in findings)
                or any(n >= HOLD_AFTER for n in strikes.values())):
            held = True
        # Outsider text that scored BLOCK_SCORE is taken down: a comment is deleted, and an issue is
        # closed and locked (only admins can delete issues). Changes made with the workflow token start
        # no new run, so this run goes on to check what is left.
        for item in blocked:
            if item is issue:
                if issue.get("state") != "closed":
                    github.call("PATCH", "/issues/%d" % number, {"state": "closed", "state_reason": "not_planned"})
                if not issue.get("locked"):
                    github.call("PUT", "/issues/%d/lock" % number, {"lock_reason": "spam"}, allow=(422,))
            else:
                github.call("DELETE", "/issues/comments/%d" % item["id"], allow=(404,))
                removed += 1
        if blocked:
            comments = [c for c in comments if all(c is not item for item in blocked)]
            fingerprint = content_fingerprint(issue, comments)
            findings = check_texts(issue, comments, complete)
        if held:
            findings = findings or [("The issue", ["review"], None)]
    # Every flag goes to the owner the same way, so a tripwire looks like any other flag.
    if findings and owner not in [a["login"] for a in issue.get("assignees") or []]:
        github.call("POST", "/issues/%d/assignees" % number, {"assignees": [owner]}, allow=(422,))

    kind = issue_type(issue)
    missing = missing_sections(issue.get("body"), kind["required"]) if kind else []

    github.set_label(number, labels, FLAGGED, bool(findings))
    github.set_label(number, labels, REVIEWED, reviewed)
    github.set_label(number, labels, CLARIFY, bool(missing))

    body = report(findings, missing, kind, fingerprint, strikes, held, reviewed, removed)
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
