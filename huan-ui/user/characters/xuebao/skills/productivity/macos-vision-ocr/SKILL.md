---
name: macos-vision-ocr
description: Use macOS Vision framework to OCR images via Swift — bypasses MiniMax API's lack of image support
category: productivity
---
# macOS Vision OCR - Swift 脚本

MiniMax API 不支持图片输入，ClawX 通过 macOS Vision 框架本地 OCR 绕过此限制。

## Swift OCR 脚本

保存到 `/tmp/ocr.swift`：

```swift
import Vision
import Foundation

let imagePath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
guard !imagePath.isEmpty else {
    print("Usage: swift /tmp/ocr.swift <image_path>")
    exit(1)
}

guard let image = CIImage(contentsOf: URL(fileURLWithPath: imagePath)) else {
    print("Failed to load image: \(imagePath)")
    exit(1)
}

let request = VNRecognizeTextRequest { request, error in
    guard error == nil,
          let observations = request.results as? [VNRecognizedTextObservation] else {
        if let error = error { print("Error: \(error)") }
        exit(1)
    }
    let text = observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    print(text.isEmpty ? "(No text found)" : text)
}

request.recognitionLevel = .accurate
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(ciImage: image, options: [:])
try handler.perform([request])
```

## 使用

```bash
swift /tmp/ocr.swift /path/to/image.png
```

## 原理

VNRecognizeTextRequest 用 macOS Vision 框架，底层在 CPU 上做 OCR，不依赖任何外部 API。输出纯文本。

## 注意事项

- 图片路径必须是完整绝对路径
- recognitionLevel 用 .accurate 模式，精度最高
- 如果中文识别不准，换 .fast 再试
