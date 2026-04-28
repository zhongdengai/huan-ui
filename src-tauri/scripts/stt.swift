import Speech
import Foundation

// 用法：stt_recognizer <音频文件路径> <结果输出文件路径> [语言代码]
// 把结果写到文件，绕过 stdout 管道缓冲

guard CommandLine.arguments.count > 2 else {
    try? "ERROR:usage".write(toFile: "/tmp/huanhuan_stt_result.txt", atomically: true, encoding: .utf8)
    exit(1)
}

let audioPath   = CommandLine.arguments[1]
let resultPath  = CommandLine.arguments[2]
let langCode    = CommandLine.arguments.count > 3 ? CommandLine.arguments[3] : "zh-CN"

func writeResult(_ text: String) {
    try? text.write(toFile: resultPath, atomically: true, encoding: .utf8)
}

guard FileManager.default.fileExists(atPath: audioPath) else {
    writeResult("ERROR:file_not_found")
    exit(1)
}

var done = false

SFSpeechRecognizer.requestAuthorization { status in
    guard status == .authorized else {
        writeResult("ERROR:not_authorized:\(status.rawValue)")
        done = true
        return
    }

    guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: langCode)),
          recognizer.isAvailable else {
        writeResult("ERROR:recognizer_unavailable")
        done = true
        return
    }

    let audioURL = URL(fileURLWithPath: audioPath)
    let request  = SFSpeechURLRecognitionRequest(url: audioURL)
    request.shouldReportPartialResults = false

    recognizer.recognitionTask(with: request) { result, error in
        if let result = result, result.isFinal {
            let text = result.bestTranscription.formattedString
            writeResult(text.isEmpty ? "ERROR:empty_result" : text)
            done = true
        } else if let error = error {
            writeResult("ERROR:\(error.localizedDescription)")
            done = true
        }
    }
}

// 用 RunLoop 驱动，不阻塞主线程，最多等 20 秒
let deadline = Date(timeIntervalSinceNow: 20)
while !done && Date() < deadline {
    RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.1))
}

if !done {
    writeResult("ERROR:timeout")
}
