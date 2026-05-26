"""截图存储层 — 管理截图文件存储与对比

支持两种存储模式：
1. 文件系统：存储 PNG 文件到指定目录
2. SQLite：存储 base64 编码的截图数据
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple


@dataclass
class ScreenshotInfo:
    """截图元数据"""

    screenshot_id: str
    session_id: str
    timestamp: int
    context: Optional[str] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None  # 文件系统路径（如果使用文件存储）
    file_size: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_base64: bool = False, base64_data: Optional[str] = None) -> Dict[str, Any]:
        """转换为字典"""
        d: Dict[str, Any] = {
            "screenshot_id": self.screenshot_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "file_size": self.file_size,
        }
        if self.context:
            d["context"] = self.context
        if self.filename:
            d["filename"] = self.filename
        if self.filepath:
            d["filepath"] = self.filepath
        if self.width is not None:
            d["width"] = self.width
        if self.height is not None:
            d["height"] = self.height
        if self.metadata:
            d["metadata"] = self.metadata
        if include_base64 and base64_data:
            d["base64"] = base64_data
        return d


@dataclass
class ScreenshotDiff:
    """截图对比结果"""

    diff_percentage: float  # 差异百分比 (0-100)
    diff_pixels: int  # 差异像素数
    total_pixels: int  # 总像素数
    diff_regions: List[Dict[str, Any]]  # 差异区域列表
    diff_image_base64: Optional[str] = None  # 差异可视化图（base64）

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "diff_percentage": round(self.diff_percentage, 4),
            "diff_pixels": self.diff_pixels,
            "total_pixels": self.total_pixels,
            "diff_regions": self.diff_regions,
            "diff_image_base64": self.diff_image_base64,
        }


class ScreenshotStorage:
    """截图存储管理器

    参数:
        db_path: SQLite 数据库路径
        storage_dir: 截图文件存储目录（None 表示仅使用 SQLite）
        use_file_system: 是否使用文件系统存储截图
    """

    def __init__(
        self,
        db_path: str = "test_observability.db",
        storage_dir: Optional[str] = None,
        use_file_system: bool = True,
    ):
        self.db_path = db_path
        self.use_file_system = use_file_system
        self._in_memory = db_path == ":memory:"
        self._conn: Optional[sqlite3.Connection] = None

        # 文件系统存储目录
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path("screenshots")
        if self.use_file_system:
            self.storage_dir.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        schema_sql = """
        CREATE TABLE IF NOT EXISTS screenshots (
            screenshot_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            context TEXT,
            filename TEXT,
            filepath TEXT,
            file_size INTEGER DEFAULT 0,
            width INTEGER,
            height INTEGER,
            base64_data TEXT,
            metadata TEXT,
            hash TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_screenshots_session ON screenshots(session_id);
        CREATE INDEX IF NOT EXISTS idx_screenshots_timestamp ON screenshots(timestamp);
        CREATE INDEX IF NOT EXISTS idx_screenshots_hash ON screenshots(hash);
        """
        with self._connect() as conn:
            conn.executescript(schema_sql)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """获取数据库连接"""
        if self._in_memory:
            # 内存数据库使用单一连接
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            conn = self._conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def store_screenshot(
        self,
        session_id: str,
        image_data: bytes,
        context: Optional[str] = None,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScreenshotInfo:
        """存储截图

        Args:
            session_id: 会话 ID
            image_data: 图片二进制数据
            context: 截图上下文描述
            filename: 文件名
            metadata: 附加元数据

        Returns:
            ScreenshotInfo 对象
        """
        screenshot_id = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)
        file_size = len(image_data)

        # 计算哈希（用于去重）
        image_hash = hashlib.sha256(image_data).hexdigest()

        # 尝试获取图片尺寸
        width, height = self._get_image_size(image_data)

        # 文件系统存储
        filepath = None
        if self.use_file_system:
            # 按 session_id 组织目录
            session_dir = self.storage_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)

            # 使用 screenshot_id 作为文件名
            if not filename:
                filename = f"{screenshot_id}.png"
            filepath = str(session_dir / filename)

            # 写入文件
            Path(filepath).write_bytes(image_data)

        # Base64 编码
        base64_data = base64.b64encode(image_data).decode("ascii")

        # 存储元数据到 SQLite
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO screenshots
                (screenshot_id, session_id, timestamp, context, filename, filepath,
                 file_size, width, height, base64_data, metadata, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    screenshot_id,
                    session_id,
                    timestamp,
                    context,
                    filename,
                    filepath,
                    file_size,
                    width,
                    height,
                    base64_data if not self.use_file_system else None,  # 文件系统模式不存 base64
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    image_hash,
                ),
            )

        return ScreenshotInfo(
            screenshot_id=screenshot_id,
            session_id=session_id,
            timestamp=timestamp,
            context=context,
            filename=filename,
            filepath=filepath,
            file_size=file_size,
            width=width,
            height=height,
            metadata=metadata or {},
        )

    def store_screenshot_base64(
        self,
        session_id: str,
        base64_data: str,
        context: Optional[str] = None,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScreenshotInfo:
        """从 base64 字符串存储截图

        Args:
            session_id: 会话 ID
            base64_data: base64 编码的图片数据
            context: 截图上下文描述
            filename: 文件名
            metadata: 附加元数据

        Returns:
            ScreenshotInfo 对象
        """
        # 解码 base64
        image_data = base64.b64decode(base64_data)
        return self.store_screenshot(
            session_id=session_id,
            image_data=image_data,
            context=context,
            filename=filename,
            metadata=metadata,
        )

    def get_screenshot(self, screenshot_id: str, include_base64: bool = False) -> Optional[Dict[str, Any]]:
        """获取截图详情

        Args:
            screenshot_id: 截图 ID
            include_base64: 是否包含 base64 数据

        Returns:
            截图信息字典，如果不存在返回 None
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            info = ScreenshotInfo(
                screenshot_id=row["screenshot_id"],
                session_id=row["session_id"],
                timestamp=row["timestamp"],
                context=row["context"],
                filename=row["filename"],
                filepath=row["filepath"],
                file_size=row["file_size"],
                width=row["width"],
                height=row["height"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            )

            base64_data = None
            if include_base64:
                if row["base64_data"]:
                    base64_data = row["base64_data"]
                elif row["filepath"]:
                    # 从文件系统读取
                    try:
                        image_data = Path(row["filepath"]).read_bytes()
                        base64_data = base64.b64encode(image_data).decode("ascii")
                    except FileNotFoundError:
                        pass

            return info.to_dict(include_base64=include_base64, base64_data=base64_data)

    def list_screenshots(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """列出会话的截图

        Args:
            session_id: 会话 ID
            limit: 返回数量限制
            offset: 偏移量

        Returns:
            截图信息列表
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM screenshots
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (session_id, limit, offset),
            )
            results = []
            for row in cursor.fetchall():
                info = ScreenshotInfo(
                    screenshot_id=row["screenshot_id"],
                    session_id=row["session_id"],
                    timestamp=row["timestamp"],
                    context=row["context"],
                    filename=row["filename"],
                    filepath=row["filepath"],
                    file_size=row["file_size"],
                    width=row["width"],
                    height=row["height"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
                results.append(info.to_dict())
            return results

    def count_screenshots(self, session_id: str) -> int:
        """统计会话截图数量"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM screenshots WHERE session_id = ?",
                (session_id,),
            )
            return cursor.fetchone()[0]

    def diff_screenshots(
        self,
        screenshot_id_1: str,
        screenshot_id_2: str,
        threshold: float = 0.01,
        generate_diff_image: bool = True,
    ) -> Optional[ScreenshotDiff]:
        """对比两张截图

        Args:
            screenshot_id_1: 第一张截图 ID
            screenshot_id_2: 第二张截图 ID
            threshold: 像素差异阈值 (0-255)，默认 0.01（即灰度差异 > 1 算不同）
            generate_diff_image: 是否生成差异可视化图

        Returns:
            ScreenshotDiff 对象，如果截图不存在返回 None
        """
        # 获取两张截图的图片数据
        img1_data = self._get_image_data(screenshot_id_1)
        img2_data = self._get_image_data(screenshot_id_2)

        if img1_data is None or img2_data is None:
            return None

        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            # 如果没有 Pillow/numpy，使用简单的哈希比较
            return self._simple_diff(screenshot_id_1, screenshot_id_2)

        # 加载图片
        img1 = Image.open(io.BytesIO(img1_data))
        img2 = Image.open(io.BytesIO(img2_data))

        # 转换为 RGB（处理 RGBA、L 等模式）
        if img1.mode != "RGB":
            img1 = img1.convert("RGB")
        if img2.mode != "RGB":
            img2 = img2.convert("RGB")

        # 调整到相同尺寸（如果不同）
        if img1.size != img2.size:
            img2 = img2.resize(img1.size)

        # 转换为 numpy 数组
        arr1 = np.array(img1, dtype=np.float32)
        arr2 = np.array(img2, dtype=np.float32)

        # 计算像素差异
        diff = np.abs(arr1 - arr2)
        # 按通道取平均差异
        diff_gray = np.mean(diff, axis=2)

        # 标记差异像素（差异 > threshold）
        diff_mask = diff_gray > threshold * 255

        total_pixels = diff_gray.size
        diff_pixels = int(np.sum(diff_mask))
        diff_percentage = (diff_pixels / total_pixels) * 100

        # 查找差异区域（连通分量）
        diff_regions = self._find_diff_regions(diff_mask)

        # 生成差异可视化图
        diff_image_base64 = None
        if generate_diff_image and diff_pixels > 0:
            diff_image_base64 = self._generate_diff_image(img1, img2, diff_mask)

        return ScreenshotDiff(
            diff_percentage=diff_percentage,
            diff_pixels=diff_pixels,
            total_pixels=total_pixels,
            diff_regions=diff_regions,
            diff_image_base64=diff_image_base64,
        )

    def delete_screenshot(self, screenshot_id: str) -> bool:
        """删除截图

        Args:
            screenshot_id: 截图 ID

        Returns:
            是否成功删除
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT filepath FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False

            # 删除文件系统文件
            if row["filepath"]:
                try:
                    Path(row["filepath"]).unlink(missing_ok=True)
                except Exception:
                    pass

            # 删除数据库记录
            conn.execute(
                "DELETE FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id,),
            )
            return True

    def delete_session_screenshots(self, session_id: str) -> int:
        """删除会话的所有截图

        Args:
            session_id: 会话 ID

        Returns:
            删除的数量
        """
        with self._connect() as conn:
            # 获取所有文件路径
            cursor = conn.execute(
                "SELECT filepath FROM screenshots WHERE session_id = ?",
                (session_id,),
            )
            filepaths = [row["filepath"] for row in cursor.fetchall() if row["filepath"]]

            # 删除文件
            for fp in filepaths:
                try:
                    Path(fp).unlink(missing_ok=True)
                except Exception:
                    pass

            # 删除数据库记录
            cursor = conn.execute(
                "DELETE FROM screenshots WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount

    # ─── 内部方法 ───────────────────────────────────────

    def _get_image_data(self, screenshot_id: str) -> Optional[bytes]:
        """获取截图的二进制数据"""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT filepath, base64_data FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            if row["filepath"]:
                try:
                    return Path(row["filepath"]).read_bytes()
                except FileNotFoundError:
                    pass

            if row["base64_data"]:
                return base64.b64decode(row["base64_data"])

            return None

    def _get_image_size(self, image_data: bytes) -> Tuple[Optional[int], Optional[int]]:
        """获取图片尺寸"""
        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(image_data))
            return img.size
        except Exception:
            return None, None

    def _simple_diff(
        self,
        screenshot_id_1: str,
        screenshot_id_2: str,
    ) -> ScreenshotDiff:
        """简单的哈希差异比较（当没有 Pillow 时使用）"""
        hash1 = None
        hash2 = None

        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT hash FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id_1,),
            )
            row = cursor.fetchone()
            if row:
                hash1 = row["hash"]

            cursor = conn.execute(
                "SELECT hash FROM screenshots WHERE screenshot_id = ?",
                (screenshot_id_2,),
            )
            row = cursor.fetchone()
            if row:
                hash2 = row["hash"]

        if hash1 is None or hash2 is None:
            return ScreenshotDiff(
                diff_percentage=100.0,
                diff_pixels=0,
                total_pixels=0,
                diff_regions=[],
            )

        # 简单比较：相同=0%，不同=100%
        is_same = hash1 == hash2
        return ScreenshotDiff(
            diff_percentage=0.0 if is_same else 100.0,
            diff_pixels=0,
            total_pixels=0,
            diff_regions=[] if is_same else [{"type": "full", "description": "Images are completely different"}],
        )

    def _find_diff_regions(self, diff_mask: Any) -> List[Dict[str, Any]]:
        """查找差异区域（简单实现：按网格划分）"""
        try:
            import numpy as np
        except ImportError:
            return []

        h, w = diff_mask.shape
        if h == 0 or w == 0:
            return []

        # 将图片划分为 4x4 网格
        grid_h = h // 4
        grid_w = w // 4
        regions = []

        for i in range(4):
            for j in range(4):
                y1 = i * grid_h
                y2 = (i + 1) * grid_h if i < 3 else h
                x1 = j * grid_w
                x2 = (j + 1) * grid_w if j < 3 else w

                cell = diff_mask[y1:y2, x1:x2]
                cell_diff_ratio = float(np.mean(cell))

                if cell_diff_ratio > 0.01:  # 超过 1% 差异的区域
                    regions.append({
                        "grid_row": i,
                        "grid_col": j,
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "diff_ratio": round(cell_diff_ratio, 4),
                    })

        return regions

    def _generate_diff_image(
        self,
        img1: Any,
        img2: Any,
        diff_mask: Any,
    ) -> Optional[str]:
        """生成差异可视化图（base64）"""
        try:
            import io

            from PIL import Image

            # 创建差异可视化图
            # 在原图上用红色标记差异区域
            diff_img = img1.copy()
            # 将差异掩码转换为红色覆盖层
            red_overlay = Image.new("RGBA", diff_img.size, (255, 0, 0, 100))
            diff_img = diff_img.convert("RGBA")
            diff_img = Image.alpha_composite(diff_img, red_overlay)

            # 转换回 RGB
            diff_img = diff_img.convert("RGB")

            # 编码为 base64
            buffer = io.BytesIO()
            diff_img.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("ascii")

        except Exception:
            return None


# 导入 io 用于 BytesIO
import io
