"""
RAG知识库引擎 v1.0 — 语义检索 + 自动注入上下文
───────────────────────────────────────────
让92文件470KB知识库不再需要手动read，AI自动获取最相关知识。

特性:
  • 轻量TF-IDF向量化（无外部依赖，离线可用）
  • 知识库自动索引（启动时构建）
  • 语义搜索 top-k 最相关知识
  • 自动注入到system prompt
  • 支持增量更新

用法:
  from .rag_engine import RAGEngine
  rag = RAGEngine()
  results = rag.search("容器逃逸 CVE")
  # → [("container_escape.md", 0.89, "内容..."), ...]
"""

import os, re, json, math, time, hashlib
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass, field
from collections import Counter

# ==================== 配置 ====================

KNOWLEDGE_DIRS = [
    "/opt/deepseek-bot/knowledge",
    "/opt/deepseek-bot/knowledge/secatlas",
    "/opt/deepseek-bot/knowledge/secatlas/blackmule",
    "/opt/deepseek-bot/knowledge/secatlas/2025-2026前沿威胁",
]

MAX_FILE_SIZE = 500_000  # 500KB
TOP_K = 5                # 默认返回5个最相关文档
CHUNK_SIZE = 2000        # 分块大小（字符）
CHUNK_OVERLAP = 200      # 分块重叠

# ==================== 中文分词 ====================

def _tokenize(text: str) -> List[str]:
    """简单分词：按字符+2-gram+3-gram，中英文混合"""
    tokens = []
    # 英文单词
    tokens.extend(re.findall(r'[a-zA-Z0-9_\-\.]{2,}', text))
    # 中文2-gram
    chinese = re.findall(r'[\u4e00-\u9fff]+', text)
    for seg in chinese:
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i+2])
        for i in range(len(seg) - 2):
            tokens.append(seg[i:i+3])
        tokens.append(seg)  # 完整词
    return tokens


@dataclass
class Document:
    """知识库文档"""
    path: str
    title: str
    content: str
    tokens: List[str] = field(default_factory=list)
    tfidf_vector: Dict[str, float] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.path)


class RAGEngine:
    """轻量RAG引擎 — TF-IDF + 余弦相似度"""

    def __init__(self):
        self.docs: List[Document] = []
        self.file_hashes: Dict[str, str] = {}
        self.idf: Dict[str, float] = {}
        self._indexed = False
        self._index_time = 0

    # ── 索引构建 ─────────────────────────────

    def index(self, force: bool = False) -> int:
        """构建/重建索引，返回文档数"""
        # 检查是否需要重建
        current_hash = self._compute_global_hash()
        if not force and self._indexed and current_hash == self._get_saved_hash():
            return len(self.docs)

        self.docs = []
        self.file_hashes = {}

        for kdir in KNOWLEDGE_DIRS:
            kpath = Path(kdir)
            if not kpath.exists():
                continue
            for fpath in kpath.rglob("*.md"):
                if fpath.stat().st_size > MAX_FILE_SIZE:
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if not content.strip():
                    continue

                title = fpath.stem
                # 尝试从内容提取标题
                m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                if m:
                    title = m.group(1).strip()

                # 分块
                chunks = self._chunk(content, CHUNK_SIZE, CHUNK_OVERLAP)
                for i, chunk in enumerate(chunks):
                    doc = Document(
                        path=str(fpath.relative_to(kpath)),
                        title=f"{title}#{i}" if len(chunks) > 1 else title,
                        content=chunk,
                        tokens=_tokenize(chunk),
                    )
                    self.docs.append(doc)

        # 计算IDF
        self._compute_idf()
        # 计算每个文档的TF-IDF向量
        for doc in self.docs:
            doc.tfidf_vector = self._tfidf(doc.tokens)

        self._indexed = True
        self._index_time = time.time()
        self._save_hash(current_hash)
        return len(self.docs)

    def _chunk(self, text: str, size: int, overlap: int) -> List[str]:
        """将文本分割为重叠块"""
        if len(text) <= size:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = start + size
            chunks.append(text[start:end])
            start = end - overlap
        return chunks

    def _compute_idf(self):
        """计算IDF（逆文档频率）"""
        N = len(self.docs)
        df = Counter()
        for doc in self.docs:
            unique_tokens = set(doc.tokens)
            for t in unique_tokens:
                df[t] += 1
        self.idf = {
            t: math.log((N + 1) / (df[t] + 1)) + 1
            for t in df
        }

    def _tfidf(self, tokens: List[str]) -> Dict[str, float]:
        """计算TF-IDF向量"""
        tf = Counter(tokens)
        total = len(tokens) or 1
        return {
            t: (tf[t] / total) * self.idf.get(t, 1.0)
            for t in set(tokens)
        }

    # ── 搜索 ─────────────────────────────────

    def search(self, query: str, top_k: int = TOP_K) -> List[Tuple[str, str, float, str]]:
        """
        语义搜索知识库
        返回: [(文件路径, 标题, 相似度, 匹配内容片段), ...]
        """
        if not self._indexed:
            self.index()

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        query_vec = self._tfidf(query_tokens)

        scores = []
        for doc in self.docs:
            score = self._cosine_similarity(query_vec, doc.tfidf_vector)
            if score > 0.01:
                # 提取最相关片段
                snippet = self._extract_snippet(doc.content, query_tokens, 300)
                scores.append((doc.path, doc.title, score, snippet))

        scores.sort(key=lambda x: x[2], reverse=True)
        return scores[:top_k]

    def _cosine_similarity(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        """余弦相似度"""
        intersection = set(v1.keys()) & set(v2.keys())
        if not intersection:
            return 0.0
        dot = sum(v1[k] * v2[k] for k in intersection)
        norm1 = math.sqrt(sum(v * v for v in v1.values()))
        norm2 = math.sqrt(sum(v * v for v in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def _extract_snippet(self, content: str, query_tokens: List[str], length: int) -> str:
        """提取包含查询词的最相关片段"""
        best_start = 0
        best_score = 0
        for t in query_tokens:
            idx = content.lower().find(t.lower())
            if idx >= 0:
                start = max(0, idx - length // 2)
                end = min(len(content), idx + length // 2)
                snippet = content[start:end]
                score = sum(1 for t2 in query_tokens if t2.lower() in snippet.lower())
                if score > best_score:
                    best_score = score
                    best_start = start
        if best_score == 0:
            return content[:length]
        return content[best_start:best_start + length]

    # ── 上下文注入 ──────────────────────────

    def inject_context(self, user_message: str, max_chars: int = 3000) -> str:
        """
        根据用户消息自动检索知识库，返回注入用的上下文字符串。
        直接追加到system prompt末尾。
        """
        results = self.search(user_message, top_k=3)
        if not results:
            return ""

        lines = ["\n\n【知识库自动匹配 — 以下内容来自你的知识库，可直接引用】"]
        total = 0
        for path, title, score, snippet in results:
            if total > max_chars:
                break
            entry = f"\n📄 {path} ({title}) [相似度:{score:.2f}]\n> {snippet[:500]}"
            lines.append(entry)
            total += len(entry)

        return "\n".join(lines)

    # ── 统计 ─────────────────────────────────

    def stats(self) -> Dict:
        """返回索引统计信息"""
        if not self._indexed:
            self.index()
        return {
            "documents": len(self.docs),
            "unique_files": len(set(d.path for d in self.docs)),
            "index_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._index_time)),
            "vocabulary_size": len(self.idf),
        }

    # ── 哈希管理 ─────────────────────────────

    def _compute_global_hash(self) -> str:
        """计算所有知识库文件的哈希"""
        h = hashlib.md5()
        for kdir in KNOWLEDGE_DIRS:
            kpath = Path(kdir)
            if not kpath.exists():
                continue
            for fpath in sorted(kpath.rglob("*.md")):
                h.update(str(fpath.stat().st_mtime).encode())
        return h.hexdigest()

    def _get_saved_hash(self) -> str:
        hf = Path("/opt/deepseek-bot/.rag_hash")
        if hf.exists():
            return hf.read_text().strip()
        return ""

    def _save_hash(self, h: str):
        Path("/opt/deepseek-bot/.rag_hash").write_text(h)


# ==================== 全局单例 ====================

_rag_instance: Optional[RAGEngine] = None


def get_rag() -> RAGEngine:
    """获取RAG引擎单例"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGEngine()
        _rag_instance.index()
    return _rag_instance


def rag_search(query: str, top_k: int = 5) -> List[Tuple[str, str, float, str]]:
    """快捷搜索"""
    return get_rag().search(query, top_k)


def rag_inject(user_message: str, max_chars: int = 3000) -> str:
    """快捷注入"""
    return get_rag().inject_context(user_message, max_chars)


def rag_stats() -> Dict:
    """快捷统计"""
    return get_rag().stats()
