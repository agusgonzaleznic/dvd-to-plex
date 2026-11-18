// Read the text in subtitle images with macOS Vision, on the device.
//
// usage: read_text LANGUAGE SCALE CORRECTION IMAGE...
//   LANGUAGE    two-letter code; the first Vision language starting with it is used (de -> de-DE)
//   SCALE       1 reads each image as it is; 2 draws it at twice the size first
//   CORRECTION  on or off: Vision's language correction
// Prints one JSON object per image, in order: {"image": path, "lines": [text, ...]}, the lines
// top to bottom. Exit status 3 when Vision has no language for LANGUAGE.
import Foundation
import ImageIO
import Vision

func fail(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count >= 4, let scale = Int(args[2]), scale >= 1, ["on", "off"].contains(args[3]) else {
    fail("usage: read_text LANGUAGE SCALE CORRECTION IMAGE...", 2)
}
let probe = VNRecognizeTextRequest()
probe.recognitionLevel = .accurate
let supported = (try? probe.supportedRecognitionLanguages()) ?? []
guard let language = supported.first(where: { $0.lowercased().hasPrefix(args[1].lowercased()) }) else {
    fail("Vision reads no language for \(args[1]); it reads \(supported.joined(separator: ", "))", 3)
}

func load(_ path: String) -> CGImage {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("cannot read \(path)", 1) }
    if scale == 1 { return image }
    let w = image.width * scale, h = image.height * scale
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpaceCreateDeviceGray(),
                              bitmapInfo: CGImageAlphaInfo.none.rawValue) else { fail("no context", 1) }
    ctx.interpolationQuality = .high
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    guard let scaled = ctx.makeImage() else { fail("cannot scale \(path)", 1) }
    return scaled
}

for path in args.dropFirst(4) {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = [language]
    request.usesLanguageCorrection = args[3] == "on"
    do {
        try VNImageRequestHandler(cgImage: load(path), options: [:]).perform([request])
    } catch {
        fail("Vision failed on \(path): \(error)", 1)
    }
    let observations = (request.results ?? []).sorted { $0.boundingBox.maxY > $1.boundingBox.maxY }
    let lines = observations.compactMap { $0.topCandidates(1).first?.string }
    let json = try! JSONSerialization.data(withJSONObject: ["image": path, "lines": lines])
    print(String(data: json, encoding: .utf8)!)
}
