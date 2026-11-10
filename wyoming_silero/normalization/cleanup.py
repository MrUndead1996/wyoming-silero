"""Bounded incremental cleanup. Plain text is emitted without waiting for EOS."""
import html
import re


class StreamingCleaner:
    def __init__(self, max_buffer=4096):
        self.max_buffer = max(32, max_buffer)
        self._pending = ""
        self._mode = "text"
        self._label = ""
        self._depth = 0
        self._line_start = True

    def add(self, chunk):
        self._pending += chunk
        return self._drain(False)

    def finish(self):
        result = self._drain(True)
        if self._mode in {"label", "after-label"}:
            result += self._label
        self._pending = self._label = ""
        self._mode = "text"
        self._line_start = True
        return result

    def _drain(self, final):
        data, out, i = self._pending, [], 0
        while i < len(data):
            tail = data[i:]
            lower = tail.lower()
            if self._mode in {"code", "think", "html-comment"}:
                close = {"code": "```", "think": "</think>", "html-comment": "-->"}[self._mode]
                end = lower.find(close)
                if end >= 0:
                    i += end + len(close)
                    self._mode = "text"
                    out.append(" ")
                    continue
                # Keep only a possible delimiter prefix, never the hidden body.
                keep = max((n for n in range(1, len(close)) if lower.endswith(close[:n])), default=0)
                i = len(data) - keep if not final else len(data)
                break
            if self._mode == "url":
                char = data[i]
                if char == "(":
                    self._depth += 1
                elif char == ")":
                    self._depth -= 1
                    if self._depth == 0:
                        self._mode = "text"
                i += 1
                continue
            if self._mode == "after-label":
                out.append(self._label)
                self._label = ""
                if data[i] == "(":
                    self._mode, self._depth = "url", 1
                    i += 1
                else:
                    self._mode = "text"
                continue
            if self._mode == "label":
                char = data[i]
                i += 1
                if char == "]":
                    self._mode = "after-label"
                elif char not in "*_`":
                    self._label += char
                    if len(self._label) >= self.max_buffer:
                        out.append(self._label)
                        self._label = ""
                continue
            # Incomplete tokens need only a tiny lookahead across chunk boundaries.
            starters = ("```", "<think>", "<!--", "![")
            if not final and any(token.startswith(lower) for token in starters):
                break
            if lower.startswith("<think>"):
                self._mode = "think"
                i += 7
                continue
            think = re.match(r"<think\s[^>]*>", tail, re.I)
            if think:
                self._mode = "think"
                i += len(think[0])
                continue
            if lower.startswith("<!--"):
                self._mode = "html-comment"
                i += 4
                continue
            if tail.startswith("```"):
                self._mode = "code"
                i += 3
                continue
            if tail.startswith("!["):
                self._mode = "label"
                i += 2
                continue
            char = data[i]
            if char == "[":
                self._mode = "label"
                i += 1
                continue
            if char == "&":
                entity = re.match(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z]+);", tail)
                if entity:
                    out.append(html.unescape(entity[0]))
                    i += len(entity[0])
                    continue
                if not final and len(tail) < 32 and re.fullmatch(r"&[#\w]*", tail):
                    break
            if char == "<":
                tag = re.match(r"</?[A-Za-z][^>]*>", tail)
                if tag:
                    i += len(tag[0])
                    continue
                if not final and len(tail) < 128 and re.match(r"</?[A-Za-z]", tail):
                    break
            if self._line_start:
                marker = re.match(r"(?:#{1,6}|[-+*>]|\d+[.)])(?=\s)", tail)
                if marker:
                    i += len(marker[0])
                    continue
                if not final and len(tail) < 12 and re.fullmatch(r"(?:#{1,6}|[-+*>]|\d+[.)]?)", tail):
                    break
            if (char not in "*_`~" and (char >= " " or char in "\n\t")
                    and char not in "\u200b\ufeff\u200d\ufe0f" and not 0x1F000 <= ord(char) <= 0x1FAFF):
                out.append(char)
            if char == "\n":
                self._line_start = True
            elif not char.isspace():
                self._line_start = False
            i += 1
        self._pending = data[i:]
        return "".join(out)
