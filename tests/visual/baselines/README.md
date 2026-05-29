# Visual Regression Baselines

本目录存放视觉回归测试的 golden-image 基线截图。

## 工作原理

1. **首次运行**：`VisualAssertions.assert_no_visual_regression()` 检测到基线不存在时，自动将当前截图保存为基线
2. **后续运行**：截图与基线进行结构相似度 (SSIM) 对比，差异超过阈值则报错
3. **更新基线**：删除对应 `.png` 文件，下次测试自动重建

## 文件命名

基线以 test_name 命名：
```
baselines/
├── battle_ui.png       # assert_no_visual_regression("battle_ui", ...)
├── main_menu.png       # assert_no_visual_regression("main_menu", ...)
└── inventory_panel.png
```

## 阈值配置

```python
# 默认 5% 容差（SSIM >= 0.95 通过）
va.assert_no_visual_regression("test_name", screenshot_path)

# 自定义容差
va.assert_no_visual_regression("test_name", screenshot_path, tolerance=0.10)
```

## 管理策略

| 场景 | 操作 |
|------|------|
| 新增测试 | 直接运行，基线自动创建 |
| UI 有意变更 | 删除对应 `.png`，重新运行生成新基线 |
| 不同平台渲染差异 | 使用 `pytest.mark.visual` + CI 环境变量控制 |

## Git 策略

- 基线 `.png` 文件**不提交**（见 `.gitignore`）
- 基线在 CI 中通过首次运行自动创建
- 本地开发时基线自动管理，无需手动维护
- 如需共享基线，使用 Git LFS（见下方配置）

## Git LFS 配置（可选）

如果团队需要共享基线，取消以下 `.gitattributes` 配置的注释：

```gitattributes
tests/visual/baselines/*.png filter=lfs diff=lfs merge=lfs -text
```
