// Render one subtitle line into a full-frame PNG for spumux: white text with a black outline on a
// transparent background, no anti-aliasing, so the image holds at most three colours.
//
// usage: swift render_text.swift OUT.png WIDTH HEIGHT TEXT [RRGGBB]   (text colour, default ffffff)
import AppKit
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
let usage = "usage: render_text.swift OUT.png WIDTH HEIGHT TEXT [RRGGBB]\n"
guard args.count == 5 || args.count == 6, let width = Int(args[2]), let height = Int(args[3]),
      let rgb = UInt32(args.count == 6 ? args[5] : "ffffff", radix: 16)
else {
    FileHandle.standardError.write(usage.data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: args[1]), text = args[4]
let fill = NSColor(deviceRed: CGFloat((rgb >> 16) & 255) / 255, green: CGFloat((rgb >> 8) & 255) / 255,
                   blue: CGFloat(rgb & 255) / 255, alpha: 1)

guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                          bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }
ctx.clear(CGRect(x: 0, y: 0, width: width, height: height))
ctx.setShouldAntialias(false)
ctx.setAllowsFontSmoothing(false)
NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
let style = NSMutableParagraphStyle()
style.alignment = .center
let attrs: [NSAttributedString.Key: Any] = [
    .font: NSFont.boldSystemFont(ofSize: 34), .foregroundColor: fill,
    .strokeColor: NSColor.black, .strokeWidth: -4.0, .paragraphStyle: style]
NSAttributedString(string: text, attributes: attrs)
    .draw(in: NSRect(x: 0, y: CGFloat(height) * 0.08, width: CGFloat(width), height: 60))

guard let image = ctx.makeImage(),
      let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)
else { exit(1) }
CGImageDestinationAddImage(dest, image, nil)
exit(CGImageDestinationFinalize(dest) ? 0 : 1)
