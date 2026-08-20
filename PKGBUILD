# Maintainer: James <claude@jamessparkes.com>
pkgname=clipcut
pkgver=3.9.17
pkgrel=1
pkgdesc="Quick drag-and-drop video trim/crop tool that preserves the source's modified date"
arch=('any')
license=('MIT')
depends=('pyside6' 'ffmpeg')
source=('clipcut' 'clipcut.desktop')
sha256sums=('SKIP' 'SKIP')

package() {
    install -Dm755 "$srcdir/clipcut" "$pkgdir/usr/bin/clipcut"
    install -Dm644 "$srcdir/clipcut.desktop" "$pkgdir/usr/share/applications/clipcut.desktop"
}
