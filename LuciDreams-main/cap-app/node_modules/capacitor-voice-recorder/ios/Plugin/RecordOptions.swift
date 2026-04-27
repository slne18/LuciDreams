import Foundation

struct RecordOptions {

    let directory: String?
    var subDirectory: String?

    mutating func setSubDirectory(to subDirectory: String) {
      self.subDirectory = subDirectory
    }

}
