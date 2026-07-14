#!/usr/bin/env swift
// ocr_check.swift — Run macOS Vision OCR on an image and print recognized text.
// Output: one line per recognized string. Exit 0 on success.
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write("usage: ocr_check.swift <image>\n".data(using: .utf8)!)
    exit(2)
}
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let cg = rep.cgImage else {
    FileHandle.standardError.write("ocr: cannot load image\n".data(using: .utf8)!)
    exit(1)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
if #available(macOS 11.0, *) {
    req.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
}
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([req])
} catch {
    FileHandle.standardError.write("ocr: vision failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}
let observations = req.results ?? []
for obs in observations {
    if let top = obs.topCandidates(1).first {
        print(top.string)
    }
}
exit(0)
