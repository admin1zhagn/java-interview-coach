# 说明：按技术分类和候选人级别搜索细粒度 JSON 分片，避免每次加载完整宝典。
import argparse
import json
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BIBLE_DIR = SCRIPT_DIR.parent / "references" / "interview-bible"
FINE_INDEX_DIR = BIBLE_DIR / "fine-index"
MANIFEST_JSON = FINE_INDEX_DIR / "manifest.json"
ARTICLES_JSON = BIBLE_DIR / "articles.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_json(path):
    """读取 JSON 文件，统一使用 UTF-8。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize(value):
    """统一大小写，便于中英文关键词匹配。"""
    return str(value or "").lower()


def split_terms(query):
    """按空格和常见标点切分查询词，同时保留完整查询。"""
    query = str(query or "").strip()
    parts = [item for item in re.split(r"[\s,，;；/、|]+", query) if item]
    if query and query not in parts:
        parts.insert(0, query)
    return parts


def score_text(text, terms):
    """计算简单关键词命中分。标题、摘要、摘录由调用方自行拼接。"""
    lower = normalize(text)
    score = 0
    for term in terms:
        term = normalize(term)
        if not term:
            continue
        score += lower.count(term) * max(2, len(term))
    return score


def match_items(query, items, id_key="id"):
    """根据 query 匹配 manifest 中的分类或级别。"""
    terms = split_terms(query)
    matched = []
    for item in items:
        haystack = " ".join([
            item.get("id", ""),
            item.get("name", ""),
            " ".join(item.get("aliases", []) or []),
        ])
        score = score_text(haystack, terms)
        if score > 0:
            matched.append({id_key: item["id"], "score": score, "item": item})
    matched.sort(key=lambda row: row["score"], reverse=True)
    return matched


def load_category_articles(manifest, query, category_ids):
    """加载命中的技术分类分片；没有指定时，根据 query 自动匹配分类。"""
    categories = manifest["categories"]
    selected = []
    if category_ids:
        selected = [item for item in categories if item["id"] in category_ids]
    else:
        auto = match_items(query, categories)
        selected = [row["item"] for row in auto[:5]]

    if not selected:
        # 找不到技术分类时使用轻量总索引，仍然不加载全文 articles.json。
        return load_json(FINE_INDEX_DIR / "all_articles_index.json"), []

    articles = []
    for category in selected:
        data = load_json(FINE_INDEX_DIR / category["file"])
        for article in data["articles"]:
            article["_source_category"] = category["id"]
            articles.append(article)
    return articles, selected


def load_level_slug_set(manifest, level_ids):
    """加载候选人级别分片，返回 slug 集合。"""
    if not level_ids:
        return None, []
    selected = [item for item in manifest["levels"] if item["id"] in level_ids]
    slugs = set()
    for level in selected:
        data = load_json(FINE_INDEX_DIR / level["file"])
        for article in data["articles"]:
            slugs.add(article["slug"])
    return slugs, selected


def rank_articles(articles, query, level_slugs, strict_level, limit, snippet_size):
    """对候选文章排序。级别默认只加权，strict_level 为 true 时才硬过滤。"""
    terms = split_terms(query)
    unique = {}
    for article in articles:
        unique[article["slug"]] = article

    results = []
    for article in unique.values():
        if strict_level and level_slugs is not None and article["slug"] not in level_slugs:
            continue
        haystack = " ".join([
            article.get("title", ""),
            " ".join(article.get("toc_path", []) or []),
            " ".join(article.get("keywords", []) or []),
            article.get("summary", ""),
            article.get("excerpt", ""),
            article.get("primary_category_name", ""),
            article.get("question_type_name", ""),
            " ".join(article.get("level_names", []) or []),
        ])
        score = score_text(haystack, terms)
        if level_slugs is not None and article["slug"] in level_slugs:
            score += 30
        if score <= 0 and query:
            continue
        results.append({
            "score": score,
            "title": article.get("title"),
            "slug": article.get("slug"),
            "url": article.get("url"),
            "category": article.get("primary_category_name"),
            "category_id": article.get("primary_category"),
            "question_type": article.get("question_type_name"),
            "levels": article.get("level_names", []),
            "toc_path": article.get("toc_path", []),
            "word_count": article.get("word_count"),
            "keywords": article.get("keywords", []),
            "snippet": (article.get("excerpt") or article.get("summary") or "")[:snippet_size],
        })

    results.sort(key=lambda row: row["score"], reverse=True)
    return results[:limit]


def list_manifest(manifest):
    """输出可用分类和级别，便于调用方选择过滤条件。"""
    print(json.dumps({
        "categories": [
            {key: item[key] for key in ["id", "name", "count", "aliases"]}
            for item in manifest["categories"]
        ],
        "levels": [
            {key: item[key] for key in ["id", "name", "count", "aliases"]}
            for item in manifest["levels"]
        ],
    }, ensure_ascii=False, indent=2))


def fetch_full_article(slug):
    """按 slug 回查完整正文；只在确实需要深挖某篇文章时使用。"""
    articles = load_json(ARTICLES_JSON)
    for article in articles:
        if article.get("slug") == slug:
            detail = article.get("detail") or {}
            print(json.dumps({
                "slug": slug,
                "title": detail.get("title") or article.get("title"),
                "word_count": detail.get("word_count") or article.get("word_count"),
                "content": detail.get("content") or "",
            }, ensure_ascii=False, indent=2))
            return
    raise SystemExit(f"未找到文章：{slug}")


def main():
    parser = argparse.ArgumentParser(description="搜索后端面试宝典细分类索引")
    parser.add_argument("query", nargs="?", default="", help="搜索关键词，例如 Redis 缓存一致性")
    parser.add_argument("--category", action="append", default=[], help="技术分类 ID，可重复传入")
    parser.add_argument("--level", action="append", default=[], help="级别 ID：campus/junior/middle/senior/expert/general，可重复传入")
    parser.add_argument("--strict-level", action="store_true", help="启用级别硬过滤；默认只加权不硬过滤")
    parser.add_argument("--limit", type=int, default=8, help="返回条数")
    parser.add_argument("--snippet-size", type=int, default=500, help="片段长度")
    parser.add_argument("--list", action="store_true", help="列出所有技术分类和级别索引")
    parser.add_argument("--fetch-slug", default="", help="按 slug 读取完整文章正文")
    args = parser.parse_args()

    if args.fetch_slug:
        fetch_full_article(args.fetch_slug)
        return

    manifest = load_json(MANIFEST_JSON)
    if args.list:
        list_manifest(manifest)
        return

    articles, selected_categories = load_category_articles(manifest, args.query, set(args.category))
    level_slugs, selected_levels = load_level_slug_set(manifest, set(args.level))
    results = rank_articles(articles, args.query, level_slugs, args.strict_level, args.limit, args.snippet_size)

    print(json.dumps({
        "query": args.query,
        "selected_categories": [{"id": item["id"], "name": item["name"], "count": item["count"]} for item in selected_categories],
        "selected_levels": [{"id": item["id"], "name": item["name"], "count": item["count"]} for item in selected_levels],
        "strict_level": args.strict_level,
        "result_count": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
