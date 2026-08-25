# Maintainer: James <claude@jamessparkes.com>
pkgname=clipcut
pkgver=3.16.1
pkgrel=2
pkgdesc="Quick drag-and-drop video trim/crop tool that preserves the source's modified date"
arch=('any')
license=('custom:proprietary')
depends=('pyside6' 'ffmpeg' 'gifsicle')
source=('clipcut' 'clipcut.desktop' 'clipcut.png' 'LICENSE')
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP')

package() {
    install -Dm755 "$srcdir/clipcut" "$pkgdir/usr/bin/clipcut"
    # The app itself lives in clipcutlib/ at the repo root, not in source=
    # (no build step here - see the top-level clipcut launcher's own
    # docstring for why the package is named clipcutlib, not clipcut, and
    # why it's not under a src/ subdirectory - that collided with
    # makepkg's own default build-staging directory of the same name).
    # $startdir is the directory containing this PKGBUILD, always available
    # in package() regardless of how source= entries are staged.
    install -d "$pkgdir/usr/lib/clipcut"
    cp -r "$startdir/clipcutlib" "$pkgdir/usr/lib/clipcut/clipcutlib"
    find "$pkgdir/usr/lib/clipcut" -name '__pycache__' -type d -exec rm -rf {} +
    find "$pkgdir/usr/lib/clipcut" -type f -name '*.py' -exec chmod 644 {} \;
    find "$pkgdir/usr/lib/clipcut" -type d -exec chmod 755 {} \;
    install -Dm644 "$srcdir/clipcut.desktop" "$pkgdir/usr/share/applications/clipcut.desktop"
    install -Dm644 "$srcdir/clipcut.png" "$pkgdir/usr/share/icons/hicolor/512x512/apps/clipcut.png"
    install -Dm644 "$srcdir/clipcut.png" "$pkgdir/usr/share/pixmaps/clipcut.png"
    install -Dm644 "$srcdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
