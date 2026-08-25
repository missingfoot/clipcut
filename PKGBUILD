# Maintainer: James <claude@jamessparkes.com>
pkgname=clipcut
pkgver=3.16.1
pkgrel=1
pkgdesc="Quick drag-and-drop video trim/crop tool that preserves the source's modified date"
arch=('any')
license=('custom:proprietary')
depends=('pyside6' 'ffmpeg' 'gifsicle')
source=('clipcut' 'clipcut.desktop' 'clipcut.png' 'LICENSE')
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP')

package() {
    install -Dm755 "$srcdir/clipcut" "$pkgdir/usr/bin/clipcut"
    install -Dm644 "$srcdir/clipcut.desktop" "$pkgdir/usr/share/applications/clipcut.desktop"
    install -Dm644 "$srcdir/clipcut.png" "$pkgdir/usr/share/icons/hicolor/512x512/apps/clipcut.png"
    install -Dm644 "$srcdir/clipcut.png" "$pkgdir/usr/share/pixmaps/clipcut.png"
    install -Dm644 "$srcdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
