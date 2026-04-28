#!/usr/bin/env python3
"""
黑色背景抠图脚本
方案：从四角洪水填充黑色区域 + 边界渐变羽化
"""

import sys
import os
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, binary_dilation
from collections import deque

def flood_fill_black_mask(img_array, threshold=30, border_px=8, erode_px=2):
    h, w = img_array.shape[:2]
    visited = np.zeros((h, w), dtype=bool)
    mask = np.zeros((h, w), dtype=np.float32)  # 1.0 = background (to remove)

    def is_black(y, x):
        r, g, b = img_array[y, x, :3]
        return int(r) + int(g) + int(b) < threshold * 3

    # 从四条边出发洪水填充
    queue = deque()
    for x in range(w):
        if not visited[0, x] and is_black(0, x):
            queue.append((0, x))
            visited[0, x] = True
        if not visited[h-1, x] and is_black(h-1, x):
            queue.append((h-1, x))
            visited[h-1, x] = True
    for y in range(h):
        if not visited[y, 0] and is_black(y, 0):
            queue.append((y, 0))
            visited[y, 0] = True
        if not visited[y, w-1] and is_black(y, w-1):
            queue.append((y, w-1))
            visited[y, w-1] = True

    while queue:
        y, x = queue.popleft()
        mask[y, x] = 1.0
        for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
            ny, nx = y+dy, x+dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_black(ny, nx):
                visited[ny, nx] = True
                queue.append((ny, nx))

    # 向内侵蚀 erode_px 像素，去掉黑边
    if erode_px > 0:
        struct = np.ones((erode_px * 2 + 1, erode_px * 2 + 1), dtype=bool)
        mask = binary_dilation(mask.astype(bool), structure=struct).astype(np.float32)

    # 高斯羽化边界
    if border_px > 0:
        mask_blur = gaussian_filter(mask, sigma=border_px / 2.5)
        mask = np.clip(mask_blur, 0, 1)

    return mask

def process_frame(input_path, output_path, threshold=30, border_px=8, erode_px=2):
    img = Image.open(input_path).convert("RGBA")
    arr = np.array(img, dtype=np.float32)

    bg_mask = flood_fill_black_mask(arr.astype(np.uint8), threshold=threshold, border_px=border_px, erode_px=erode_px)

    # alpha = 原alpha * (1 - background_mask)
    arr[:, :, 3] = arr[:, :, 3] * (1.0 - bg_mask)

    result = Image.fromarray(arr.astype(np.uint8), "RGBA")
    result.save(output_path)

def main():
    frames_dir = sys.argv[1] if len(sys.argv) > 1 else "/Users/jiaqi/Documents/huanhuan/huan-ui/user/characters/huanhuan/frames"
    threshold = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    border_px = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    erode_px = int(sys.argv[4]) if len(sys.argv) > 4 else 2

    frames = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
    total = len(frames)
    print(f"处理 {total} 帧，阈值={threshold}，侵蚀={erode_px}px，羽化={border_px}px")

    for i, fname in enumerate(frames, 1):
        path = os.path.join(frames_dir, fname)
        process_frame(path, path, threshold=threshold, border_px=border_px, erode_px=erode_px)
        if i % 10 == 0 or i == total:
            print(f"  {i}/{total} 完成")

    print("全部完成！")

if __name__ == "__main__":
    main()
