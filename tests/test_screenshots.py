"""截图管理服务测试

测试截图上传、列表、详情、对比等功能。
"""

import base64
import io
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# 创建测试用的 PNG 图片
def create_test_png(width: int = 100, height: int = 100, color: tuple = (255, 0, 0)) -> bytes:
    """创建测试用 PNG 图片"""
    from PIL import Image
    img = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def create_gradient_png(width: int = 100, height: int = 100) -> bytes:
    """创建渐变测试图片"""
    from PIL import Image
    img = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(height):
            img.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# ─── Fixtures ──────────────────────────────────────────


@pytest.fixture
def app():
    """创建测试用 FastAPI 应用"""
    from fastapi import FastAPI
    from gateway.screenshot_storage import ScreenshotStorage
    from gateway.storage import Storage

    app = FastAPI()

    # 使用内存数据库
    storage = Storage(db_path=":memory:")
    screenshot_storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)

    app.state.storage = storage
    app.state.screenshot_storage = screenshot_storage

    # 注册路由
    from gateway.routes.screenshots import router
    app.include_router(router)

    # 创建测试 session
    from schema.events import ObsSession
    session = ObsSession(
        session_id="test-session-1",
        project="test-project",
        framework="godot_driver",
        started_at=int(time.time() * 1000),
    )
    storage.store_session(session)

    return app


@pytest.fixture
def client(app):
    """创建测试客户端"""
    return TestClient(app)


@pytest.fixture
def sample_image():
    """示例图片数据"""
    return create_test_png(100, 100, (255, 0, 0))


@pytest.fixture
def sample_image_2():
    """第二个示例图片（不同颜色）"""
    return create_test_png(100, 100, (0, 255, 0))


@pytest.fixture
def sample_image_base64(sample_image):
    """示例图片的 base64 编码"""
    return base64.b64encode(sample_image).decode("ascii")


# ─── 上传截图测试 ──────────────────────────────────────


class TestUploadScreenshot:
    def test_upload_base64(self, client, sample_image_base64):
        """测试通过 base64 上传截图"""
        response = client.post(
            "/sessions/test-session-1/screenshots",
            json={
                "base64_data": sample_image_base64,
                "context": "test_screenshot",
                "filename": "test.png",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "screenshot" in data
        assert data["screenshot"]["session_id"] == "test-session-1"
        assert data["screenshot"]["context"] == "test_screenshot"
        assert data["screenshot"]["filename"] == "test.png"
        assert data["screenshot"]["width"] == 100
        assert data["screenshot"]["height"] == 100

    def test_upload_base64_minimal(self, client, sample_image_base64):
        """测试最小参数的 base64 上传"""
        response = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["screenshot"]["session_id"] == "test-session-1"

    def test_upload_file(self, client, sample_image):
        """测试通过文件上传截图"""
        response = client.post(
            "/sessions/test-session-1/screenshots",
            files={"file": ("test.png", sample_image, "image/png")},
            data={"context": "file_upload"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["screenshot"]["context"] == "file_upload"

    def test_upload_no_data(self, client):
        """测试没有数据时上传失败"""
        response = client.post(
            "/sessions/test-session-1/screenshots",
            json={},
        )
        assert response.status_code == 400

    def test_upload_invalid_session(self, client, sample_image_base64):
        """测试不存在的 session"""
        response = client.post(
            "/sessions/nonexistent/screenshots",
            json={"base64_data": sample_image_base64},
        )
        assert response.status_code == 404

    def test_upload_invalid_base64(self, client):
        """测试无效的 base64 数据"""
        response = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": "not-valid-base64!@#$"},
        )
        assert response.status_code == 400


# ─── 列出截图测试 ──────────────────────────────────────


class TestListScreenshots:
    def test_list_empty(self, client):
        """测试列出空列表"""
        response = client.get("/sessions/test-session-1/screenshots")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["screenshots"] == []
        assert data["count"] == 0

    def test_list_with_screenshots(self, client, sample_image_base64):
        """测试列出有截图的列表"""
        # 上传 3 张截图
        for i in range(3):
            client.post(
                "/sessions/test-session-1/screenshots",
                json={
                    "base64_data": sample_image_base64,
                    "context": f"screenshot_{i}",
                },
            )

        response = client.get("/sessions/test-session-1/screenshots")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert len(data["screenshots"]) == 3

    def test_list_with_limit(self, client, sample_image_base64):
        """测试分页限制"""
        # 上传 5 张截图
        for i in range(5):
            client.post(
                "/sessions/test-session-1/screenshots",
                json={"base64_data": sample_image_base64},
            )

        response = client.get("/sessions/test-session-1/screenshots?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["screenshots"]) == 2
        assert data["count"] == 5  # 总数还是 5

    def test_list_with_offset(self, client, sample_image_base64):
        """测试分页偏移"""
        # 上传 5 张截图
        for i in range(5):
            client.post(
                "/sessions/test-session-1/screenshots",
                json={"base64_data": sample_image_base64},
            )

        response = client.get("/sessions/test-session-1/screenshots?limit=2&offset=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["screenshots"]) == 2

    def test_list_invalid_session(self, client):
        """测试不存在的 session"""
        response = client.get("/sessions/nonexistent/screenshots")
        assert response.status_code == 404


# ─── 获取截图详情测试 ────────────────────────────────


class TestGetScreenshot:
    def test_get_with_base64(self, client, sample_image_base64):
        """测试获取截图详情（包含 base64）"""
        # 上传
        upload_resp = client.post(
            "/sessions/test-session-1/screenshots",
            json={
                "base64_data": sample_image_base64,
                "context": "detail_test",
            },
        )
        screenshot_id = upload_resp.json()["screenshot"]["screenshot_id"]

        # 获取详情
        response = client.get(f"/sessions/test-session-1/screenshots/{screenshot_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["screenshot"]["screenshot_id"] == screenshot_id
        assert data["screenshot"]["context"] == "detail_test"
        assert "base64" in data["screenshot"]
        assert len(data["screenshot"]["base64"]) > 0

    def test_get_without_base64(self, client, sample_image_base64):
        """测试获取截图详情（不包含 base64）"""
        # 上传
        upload_resp = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        screenshot_id = upload_resp.json()["screenshot"]["screenshot_id"]

        # 获取详情
        response = client.get(f"/sessions/test-session-1/screenshots/{screenshot_id}?include_base64=false")
        assert response.status_code == 200
        data = response.json()
        assert "base64" not in data["screenshot"]

    def test_get_nonexistent(self, client):
        """测试获取不存在的截图"""
        response = client.get("/sessions/test-session-1/screenshots/nonexistent-id")
        assert response.status_code == 404

    def test_get_wrong_session(self, client, sample_image_base64):
        """测试从错误的 session 获取截图"""
        # 上传
        upload_resp = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        screenshot_id = upload_resp.json()["screenshot"]["screenshot_id"]

        # 用错误的 session 获取
        response = client.get(f"/sessions/wrong-session/screenshots/{screenshot_id}")
        assert response.status_code == 404


# ─── 截图对比测试 ──────────────────────────────────────


class TestScreenshotDiff:
    def test_diff_identical(self, client, sample_image_base64):
        """测试对比两张相同的截图"""
        # 上传同一张截图两次
        resp1 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64, "context": "img1"},
        )
        resp2 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64, "context": "img2"},
        )
        id1 = resp1.json()["screenshot"]["screenshot_id"]
        id2 = resp2.json()["screenshot"]["screenshot_id"]

        # 对比
        response = client.post(
            "/sessions/test-session-1/screenshots/diff",
            json={
                "screenshot_id_1": id1,
                "screenshot_id_2": id2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["diff"]["diff_percentage"] == 0.0
        assert data["diff"]["diff_pixels"] == 0

    def test_diff_different(self, client, sample_image_base64, sample_image_2):
        """测试对比两张不同的截图"""
        # 上传两张不同的截图
        image2_base64 = base64.b64encode(sample_image_2).decode("ascii")

        resp1 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64, "context": "red"},
        )
        resp2 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": image2_base64, "context": "green"},
        )
        id1 = resp1.json()["screenshot"]["screenshot_id"]
        id2 = resp2.json()["screenshot"]["screenshot_id"]

        # 对比
        response = client.post(
            "/sessions/test-session-1/screenshots/diff",
            json={
                "screenshot_id_1": id1,
                "screenshot_id_2": id2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["diff"]["diff_percentage"] == 100.0
        assert data["diff"]["diff_pixels"] == 10000  # 100x100

    def test_diff_partial(self, client):
        """测试对比部分不同的截图"""
        # 创建部分不同的图片
        from PIL import Image
        import io

        # 图片 1: 全红
        img1 = Image.new("RGB", (100, 100), (255, 0, 0))
        buf1 = io.BytesIO()
        img1.save(buf1, format="PNG")
        base64_1 = base64.b64encode(buf1.getvalue()).decode("ascii")

        # 图片 2: 左半红，右半蓝
        img2 = Image.new("RGB", (100, 100), (255, 0, 0))
        for x in range(50, 100):
            for y in range(100):
                img2.putpixel((x, y), (0, 0, 255))
        buf2 = io.BytesIO()
        img2.save(buf2, format="PNG")
        base64_2 = base64.b64encode(buf2.getvalue()).decode("ascii")

        # 上传
        resp1 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": base64_1},
        )
        resp2 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": base64_2},
        )
        id1 = resp1.json()["screenshot"]["screenshot_id"]
        id2 = resp2.json()["screenshot"]["screenshot_id"]

        # 对比
        response = client.post(
            "/sessions/test-session-1/screenshots/diff",
            json={
                "screenshot_id_1": id1,
                "screenshot_id_2": id2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        # 右半部分不同（50%）
        assert 49.0 < data["diff"]["diff_percentage"] < 51.0
        assert data["diff"]["diff_pixels"] > 0

    def test_diff_with_diff_image(self, client, sample_image_base64, sample_image_2):
        """测试对比时生成差异图"""
        image2_base64 = base64.b64encode(sample_image_2).decode("ascii")

        resp1 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        resp2 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": image2_base64},
        )
        id1 = resp1.json()["screenshot"]["screenshot_id"]
        id2 = resp2.json()["screenshot"]["screenshot_id"]

        response = client.post(
            "/sessions/test-session-1/screenshots/diff",
            json={
                "screenshot_id_1": id1,
                "screenshot_id_2": id2,
                "generate_diff_image": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "diff_image_base64" in data["diff"]
        assert data["diff"]["diff_image_base64"] is not None

    def test_diff_nonexistent(self, client, sample_image_base64):
        """测试对比不存在的截图"""
        resp1 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        id1 = resp1.json()["screenshot"]["screenshot_id"]

        response = client.post(
            "/sessions/test-session-1/screenshots/diff",
            json={
                "screenshot_id_1": id1,
                "screenshot_id_2": "nonexistent",
            },
        )
        assert response.status_code == 404

    def test_diff_wrong_session(self, client, sample_image_base64):
        """测试对比不同 session 的截图"""
        # 创建另一个 session
        from schema.events import ObsSession
        app = client.app
        session2 = ObsSession(
            session_id="test-session-2",
            project="test-project",
            framework="godot_driver",
            started_at=int(time.time() * 1000),
        )
        app.state.storage.store_session(session2)

        # 分别上传到不同 session
        resp1 = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        resp2 = client.post(
            "/sessions/test-session-2/screenshots",
            json={"base64_data": sample_image_base64},
        )
        id1 = resp1.json()["screenshot"]["screenshot_id"]
        id2 = resp2.json()["screenshot"]["screenshot_id"]

        # 对比应该失败
        response = client.post(
            "/sessions/test-session-1/screenshots/diff",
            json={
                "screenshot_id_1": id1,
                "screenshot_id_2": id2,
            },
        )
        assert response.status_code == 400


# ─── 删除截图测试 ──────────────────────────────────────


class TestDeleteScreenshot:
    def test_delete_screenshot(self, client, sample_image_base64):
        """测试删除截图"""
        # 上传
        upload_resp = client.post(
            "/sessions/test-session-1/screenshots",
            json={"base64_data": sample_image_base64},
        )
        screenshot_id = upload_resp.json()["screenshot"]["screenshot_id"]

        # 删除
        response = client.delete(f"/sessions/test-session-1/screenshots/{screenshot_id}")
        assert response.status_code == 200

        # 验证已删除
        get_resp = client.get(f"/sessions/test-session-1/screenshots/{screenshot_id}")
        assert get_resp.status_code == 404

    def test_delete_nonexistent(self, client):
        """测试删除不存在的截图"""
        response = client.delete("/sessions/test-session-1/screenshots/nonexistent")
        assert response.status_code == 404


# ─── 统计测试 ──────────────────────────────────────────


class TestScreenshotStats:
    def test_stats_empty(self, client):
        """测试空统计"""
        response = client.get("/sessions/test-session-1/screenshots/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    def test_stats_with_screenshots(self, client, sample_image_base64):
        """测试有截图的统计"""
        # 上传 3 张
        for _ in range(3):
            client.post(
                "/sessions/test-session-1/screenshots",
                json={"base64_data": sample_image_base64},
            )

        response = client.get("/sessions/test-session-1/screenshots/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3


# ─── ScreenshotStorage 单元测试 ──────────────────────


class TestScreenshotStorage:
    def test_store_screenshot(self):
        """测试存储截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50, (128, 128, 128))

        info = storage.store_screenshot(
            session_id="test",
            image_data=image_data,
            context="unit_test",
        )

        assert info.session_id == "test"
        assert info.width == 50
        assert info.height == 50
        assert info.file_size > 0

    def test_store_base64(self):
        """测试从 base64 存储"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50)
        base64_str = base64.b64encode(image_data).decode("ascii")

        info = storage.store_screenshot_base64(
            session_id="test",
            base64_data=base64_str,
        )

        assert info.session_id == "test"
        assert info.width == 50

    def test_list_screenshots(self):
        """测试列出截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50)

        # 存储 3 张
        for i in range(3):
            storage.store_screenshot("test", image_data, context=f"img_{i}")

        screenshots = storage.list_screenshots("test")
        assert len(screenshots) == 3

    def test_count_screenshots(self):
        """测试统计截图数量"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50)

        assert storage.count_screenshots("test") == 0

        storage.store_screenshot("test", image_data)
        assert storage.count_screenshots("test") == 1

        storage.store_screenshot("test", image_data)
        assert storage.count_screenshots("test") == 2

    def test_diff_identical(self):
        """测试对比相同的截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50)

        info1 = storage.store_screenshot("test", image_data)
        info2 = storage.store_screenshot("test", image_data)

        diff = storage.diff_screenshots(info1.screenshot_id, info2.screenshot_id)
        assert diff is not None
        assert diff.diff_percentage == 0.0
        assert diff.diff_pixels == 0

    def test_diff_different(self):
        """测试对比不同的截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image1 = create_test_png(50, 50, (255, 0, 0))
        image2 = create_test_png(50, 50, (0, 255, 0))

        info1 = storage.store_screenshot("test", image1)
        info2 = storage.store_screenshot("test", image2)

        diff = storage.diff_screenshots(info1.screenshot_id, info2.screenshot_id)
        assert diff is not None
        assert diff.diff_percentage == 100.0
        assert diff.diff_pixels == 2500  # 50x50

    def test_diff_nonexistent(self):
        """测试对比不存在的截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)

        diff = storage.diff_screenshots("nonexistent1", "nonexistent2")
        assert diff is None

    def test_delete_screenshot(self):
        """测试删除截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50)

        info = storage.store_screenshot("test", image_data)
        assert storage.get_screenshot(info.screenshot_id) is not None

        storage.delete_screenshot(info.screenshot_id)
        assert storage.get_screenshot(info.screenshot_id) is None

    def test_delete_session_screenshots(self):
        """测试删除会话所有截图"""
        from gateway.screenshot_storage import ScreenshotStorage

        storage = ScreenshotStorage(db_path=":memory:", use_file_system=False)
        image_data = create_test_png(50, 50)

        storage.store_screenshot("test", image_data)
        storage.store_screenshot("test", image_data)
        storage.store_screenshot("other", image_data)

        deleted = storage.delete_session_screenshots("test")
        assert deleted == 2
        assert storage.count_screenshots("test") == 0
        assert storage.count_screenshots("other") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
