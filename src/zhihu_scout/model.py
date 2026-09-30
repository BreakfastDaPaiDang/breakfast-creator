from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin, urlunsplit

from .access import ZhihuError, validate_url


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "blockquote", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if not self.hidden and tag == "img":
            self.parts.append("[图片：" + (dict(attrs).get("alt") or "无文字说明") + "]")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    if not isinstance(value, str):
        raise ZhihuError("response_changed", "正文或标题不是字符串。")
    parser = TextParser()
    parser.feed(value)
    return "\n".join(s.strip() for s in "".join(parser.parts).splitlines() if s.strip())


def ident(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ZhihuError("response_changed", "内容ID缺失或异常。")
    return str(value)


def count(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
        raise ZhihuError("response_changed", "赞数或数量字段缺失或异常。")
    return int(value)


def target(value, kind="question"):
    if value.isdigit():
        return kind, value
    u = urlsplit(value)
    if u.scheme not in {"https", "http"} or u.netloc not in {"www.zhihu.com", "zhihu.com"}:
        raise ZhihuError("invalid_input", "请输入知乎问题／回答链接或数字ID。", exit_code=2)
    m = re.fullmatch(r"/question/(\d+)(?:/answer/(\d+))?/?", u.path)
    if not m:
        raise ZhihuError("invalid_input", "当前支持问题与回答链接。", exit_code=2)
    return ("answer", m[2]) if m[2] else ("question", m[1])


def normalize(obj, kind, *, question_id=None, source_url=None, root_id=None):
    if not isinstance(obj, dict):
        raise ZhihuError("response_changed", "数据条目不是对象。")
    rid = ident(obj.get("id"))
    result = {"kind": kind, "id": rid}
    if kind == "question":
        result.update(title=plain(obj.get("title") or obj.get("name")), text=plain(obj.get("detail", "")),
                      source_url=f"https://www.zhihu.com/question/{rid}")
        for field in ("answer_count", "visit_count", "follower_count", "comment_count"):
            value = obj.get(field)
            result[field] = count(value) if value is not None else None
        result.update(created_time=obj.get("created"), updated_time=obj.get("updated_time"),
                      metrics_source="question_detail")
    elif kind == "answer":
        q = obj.get("question") or {}
        qid = ident(question_id or q.get("id"))
        content = obj.get("content")
        result.update(question_id=qid, title=plain(q.get("title") or q.get("name", "")),
                      text=plain(content if isinstance(content, str) else obj.get("excerpt", "")),
                      text_status="content" if isinstance(content, str) else "excerpt_only",
                      likes=count(obj.get("voteup_count")), comment_count=count(obj.get("comment_count")),
                      source_url=f"https://www.zhihu.com/question/{qid}/answer/{rid}")
    else:
        parent = obj.get("reply_comment_id") or root_id
        if str(parent) == "0":
            parent = root_id
        result.update(text=plain(obj.get("content")), likes=count(obj.get("like_count")),
                      reply_count=count(obj.get("child_comment_count", 0)),
                      root_id=root_id or rid, parent_id=ident(parent) if parent else None,
                      source_url=source_url)
    if kind != "question":
        author = obj.get("author") or {}
        author = author.get("member") or author
        result.update(author=author.get("name", ""), created_time=obj.get("created_time"),
                      updated_time=obj.get("updated_time"))
    return result


def search_questions(items):
    records = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            raise ZhihuError("response_changed", "搜索条目不是对象。")
        obj = item.get("object") or item
        if not isinstance(obj, dict):
            raise ZhihuError("response_changed", "搜索对象结构改变。")
        kind = obj.get("type")
        if kind == "question":
            records.append(normalize(obj, "question"))
        elif kind == "answer":
            q = obj.get("question")
            if not isinstance(q, dict):
                raise ZhihuError("response_changed", "搜索回答缺少所属问题。")
            records.append(normalize(q, "question"))
            records.append({"kind": "hit", "id": ident(obj.get("id")), "question_id": ident(q.get("id")),
                            "text": plain(obj.get("excerpt", "")), "text_status": "excerpt_only",
                            "likes": count(obj.get("voteup_count")),
                            "source_url": f"https://www.zhihu.com/question/{q['id']}/answer/{obj['id']}"})
        else:
            skipped += 1
    for record in records:
        if record["kind"] == "question":
            # Search embeds often contain placeholder zeros; do not rank by them.
            record["search_metrics"] = {key: record[key] for key in
                                        ("answer_count", "visit_count", "follower_count", "comment_count")}
            for key in record["search_metrics"]:
                record[key] = None
            record["metrics_source"] = "search_unverified"
    return records, skipped


def next_url(payload, current):
    data = payload.get("data")
    paging = payload.get("paging")
    if not isinstance(data, list) or not isinstance(paging, dict) or type(paging.get("is_end")) is not bool:
        raise ZhihuError("response_changed", "分页缺少data列表或is_end。")
    totals = next((v for v in [paging.get("totals"), payload.get("totals"),
                              (payload.get("counts") or {}).get("total_counts")] if v is not None), None)
    if not data and (not paging["is_end"] or (totals is not None and count(totals) > 0)):
        raise ZhihuError("incomplete_page", "平台显示仍有内容却返回空页。")
    if paging["is_end"]:
        return None
    value = paging.get("next")
    if not isinstance(value, str) or not value:
        raise ZhihuError("response_changed", "分页未结束但没有下一页游标。")
    url = urljoin(current, value)
    parsed, origin = urlsplit(url), urlsplit(current)
    if (parsed.scheme in {"https", "http"} and parsed.netloc == "api.zhihu.com"
            and not parsed.fragment and parsed.path == origin.path.removeprefix("/api/v4")):
        # API payloads advertise the mobile API host; keep its exact query while
        # continuing through the already authenticated web endpoint over HTTPS.
        url = urlunsplit(("https", "www.zhihu.com", origin.path, parsed.query, ""))
    elif parsed.scheme == "http" and parsed.netloc == "www.zhihu.com" and parsed.path == origin.path:
        url = urlunsplit(parsed._replace(scheme="https"))
    if validate_url(url) != validate_url(current):
        raise ZhihuError("invalid_endpoint", "下一页跳到了其他接口。")
    if url == current:
        raise ZhihuError("pagination_stalled", "下一页游标没有前进。")
    return url
