# Third-party runtime

Camera access uses [Aravis](https://github.com/AravisProject/aravis), licensed under LGPL-2.1-or-later. The development copy is Aravis 0.8.36 from the official Homebrew bottle, loaded dynamically. Its [corresponding source](https://github.com/AravisProject/aravis/tree/0.8.36) and license are available upstream. The runtime remains replaceable using `ARAVIS_LIBRARY`.

Project-local downloads live in `.vendor/`, which is excluded from version control. No FLIR/Teledyne proprietary SDK headers or binaries are copied into application source. Review applicable third-party license requirements before packaging binaries for distribution.
