// Render one line of text into a bitmap and read it back with macOS Vision, on the device.
//
// usage: swift render_and_read.swift TEXT LANGUAGE
// An empty TEXT renders a blank image, the control: Vision must read nothing from it.
// Prints each recognised line on its own line, nothing else.
import AppKit
import Vision

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write("usage: render_and_read.swift TEXT LANGUAGE\n".data(using: .utf8)!)
    exit(2)
}
let text = args[1], language = args[2]
let width = 720, height = 120

guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                          bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }
ctx.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 1))
ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
if !text.isEmpty {
    NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 40), .foregroundColor: NSColor.white]
    NSAttributedString(string: text, attributes: attrs).draw(at: NSPoint(x: 20, y: 35))
}
guard let image = ctx.makeImage() else { exit(1) }

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = [language]
try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
for observation in request.results ?? [] {
    if let line = observation.topCandidates(1).first?.string { print(line) }
}
