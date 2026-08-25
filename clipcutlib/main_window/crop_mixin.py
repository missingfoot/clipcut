"""CropMixin: crop-guide field sync, aspect-ratio presets/lock, and orientation flip."""


class CropMixin:
    def recenter_crop(self):
        """Keep the crop box's current size, just re-center it on the frame."""
        if not self.path:
            return
        left, top, right, bottom = self.preview.crop
        box_w = self.video_width - left - right
        box_h = self.video_height - top - bottom
        if box_w <= 0 or box_h <= 0:
            return
        new_left = (self.video_width - box_w) // 2
        new_right = self.video_width - box_w - new_left
        new_top = (self.video_height - box_h) // 2
        new_bottom = self.video_height - box_h - new_top
        self._syncing = True
        for edit, value in zip(self.crop_edits, (new_left, new_top, new_right, new_bottom)):
            edit.setText(str(value))
        self._syncing = False
        self.preview.set_crop_pixels(new_left, new_top, new_right, new_bottom)
        self.set_current_crop(new_left, new_top, new_right, new_bottom)
        self.update_crop_size_fields()

    def on_crop_size_field_edited(self, which):
        """Typing an exact pixel width/height overrides the crop directly, acting like a
        corner-scale: when Lock Aspect Ratio is on (and a real box already exists), the
        edited dimension drives the other one via the current ratio, same as dragging a
        corner would. Unlocked, both are independent and this breaks any active preset."""
        if self._syncing or not self.path:
            return
        self._syncing = True
        try:
            w_text = self.crop_width_edit.text()
            h_text = self.crop_height_edit.text()
            ratio = self.preview.effective_ratio()
            if ratio and which == "width" and w_text:
                target_w = int(w_text)
                target_h = target_w / ratio
            elif ratio and which == "height" and h_text:
                target_h = int(h_text)
                target_w = target_h * ratio
            else:
                target_w = int(w_text) if w_text else self.video_width
                target_h = int(h_text) if h_text else self.video_height
            if target_w <= 0 or target_h <= 0:
                raise ValueError("size must be positive")
            # Cap both dimensions together so a target that would overflow the frame on
            # one axis scales the whole box back instead of just clamping that axis and
            # breaking the ratio.
            over = max(target_w / self.video_width, target_h / self.video_height)
            if over > 1:
                target_w /= over
                target_h /= over
            target_w = int(round(target_w))
            target_h = int(round(target_h))

            left0, top0, right0, bottom0 = self.preview.crop
            center_x = (left0 + (self.video_width - right0)) / 2
            center_y = (top0 + (self.video_height - bottom0)) / 2
            # Derive the opposite edge from the total instead of rounding both edges
            # independently - two separate round()s of complementary .5 values can both
            # round the same way (Python's banker's rounding), leaving them short of the
            # target width/height entirely instead of off by a pixel.
            left = max(0, min(self.video_width - 1, int(round(center_x - target_w / 2))))
            right = max(0, min(self.video_width - 1, self.video_width - target_w - left))
            top = max(0, min(self.video_height - 1, int(round(center_y - target_h / 2))))
            bottom = max(0, min(self.video_height - 1, self.video_height - target_h - top))
            for edit, value in zip(self.crop_edits, (left, top, right, bottom)):
                edit.setText(str(value))
            if ratio:
                self.crop_width_edit.setText(str(target_w))
                self.crop_height_edit.setText(str(target_h))
                self.preview.apply_scaled_crop(left, top, right, bottom)
                self.set_current_crop(left, top, right, bottom)
            else:
                self.preview.set_crop_pixels(left, top, right, bottom)
                self.set_current_crop(left, top, right, bottom)
                self.break_aspect_lock()
        except ValueError:
            pass
        finally:
            self._syncing = False
        self.update_crop_size_fields()
        self.update_suffix_placeholder()

    def on_crop_position_field_edited(self, which):
        """Typing an exact X/Y moves the crop box - same translation dragging the crop
        interior does, so it's ratio-agnostic (a move never changes the box's shape) and
        always applies regardless of the aspect-ratio lock."""
        if self._syncing or not self.path:
            return
        self._syncing = True
        try:
            left0, top0, right0, bottom0 = self.preview.crop
            if which == "x":
                box_w = self.video_width - left0 - right0
                x_text = self.crop_x_edit.text()
                new_x = max(0, min(self.video_width - box_w, int(x_text) if x_text else 0))
                left, right = new_x, self.video_width - box_w - new_x
                top, bottom = top0, bottom0
            else:
                box_h = self.video_height - top0 - bottom0
                y_text = self.crop_y_edit.text()
                new_y = max(0, min(self.video_height - box_h, int(y_text) if y_text else 0))
                top, bottom = new_y, self.video_height - box_h - new_y
                left, right = left0, right0
            for edit, value in zip(self.crop_edits, (left, top, right, bottom)):
                edit.setText(str(value))
            self.preview.apply_scaled_crop(left, top, right, bottom)
            self.set_current_crop(left, top, right, bottom)
        except ValueError:
            pass
        finally:
            self._syncing = False
        self.update_crop_size_fields()
        self.update_suffix_placeholder()

    def clear_crop(self):
        if not self.path:
            return
        self._syncing = True
        for field in self.crop_edits:
            field.clear()
        self._syncing = False
        self.preview.set_crop_pixels(0, 0, 0, 0)
        self.set_current_crop(0, 0, 0, 0)
        self.update_suffix_placeholder()
        self.update_crop_size_fields()

    def on_crop_field_edited(self, which):
        self.update_suffix_placeholder()
        self.update_crop_size_fields()
        if self._syncing or not self.path:
            return
        self._syncing = True
        try:
            edit = {"left": self.crop_left, "top": self.crop_top,
                    "right": self.crop_right, "bottom": self.crop_bottom}[which]
            new_value = int(edit.text() or 0)
            if new_value < 0:
                raise ValueError("crop value must be >= 0")
            left, top, right, bottom, locked = self.preview.set_edge(which, new_value)
            self.set_current_crop(left, top, right, bottom)
            for target_edit, value in zip(self.crop_edits, (left, top, right, bottom)):
                target_edit.setText(str(value))
            if not locked:
                self.break_aspect_lock()
        except ValueError:
            pass
        finally:
            self._syncing = False
        self.update_crop_size_fields()

    def current_target_ratio(self):
        """The width/height ratio the Preset dropdown currently asks for, or None
        for "None". This doubles as the corner handles' lock: see VideoPreview.locked_ratio."""
        label = self.aspect_combo.currentText()
        if label == "None":
            return None
        if label == "Original":
            # The video's own native ratio - applying it always crops to
            # (0,0,0,0) (nothing needs trimming, it already matches), but
            # locks that ratio so a corner-resize scales down within the same
            # shape without the user needing to know or pick 1:1 vs 16:9 etc.
            if not self.video_width or not self.video_height:
                return None
            ratio = self.video_width / self.video_height
        else:
            num, _, den = label.partition(":")
            ratio = int(num) / int(den)
        return 1 / ratio if self.orient_vertical.isChecked() else ratio

    def match_aspect_preset(self, crop):
        """Which Preset dropdown index (0 = "None") this crop tuple exactly
        matches, given the current video size/orientation - keeps the dropdown
        honestly reflecting whatever crop is actually on screen (per cut, once
        Per-Cut Crop is on) instead of just remembering whichever preset was
        picked last, possibly for a different cut entirely.

        Treats crop == (0,0,0,0) as "None" even though some preset might
        technically also compute an all-zero crop (e.g. a video whose native
        ratio already matches "4:3") - a genuinely untouched cut ALSO displays
        as (0,0,0,0), and there's no way to tell the two apart from the crop
        value alone, so this stays on the side that doesn't silently engage an
        aspect lock nobody asked for. on_aspect_ratio_selected() overrides
        this explicitly for the one case that needs to win anyway: a preset
        the user just deliberately picked, even one that happens to produce
        an all-zero crop."""
        if crop == (0, 0, 0, 0) or not self.video_width or not self.video_height:
            return 0
        vw, vh = self.video_width, self.video_height
        for i in range(1, self.aspect_combo.count()):
            label = self.aspect_combo.itemText(i)
            if label == "Original":
                continue  # not in "W:H" text form - always resolves to (0,0,0,0) anyway,
                # already handled by the shortcut above
            num, _, den = label.partition(":")
            ratio = int(num) / int(den)
            if self.orient_vertical.isChecked():
                ratio = 1 / ratio
            if vw / vh > ratio:
                crop_w, crop_h = vw - round(vh * ratio), 0
            else:
                crop_w, crop_h = 0, vh - round(vw / ratio)
            expected = (crop_w // 2, crop_h // 2, crop_w - crop_w // 2, crop_h - crop_h // 2)
            if crop == expected:
                return i
        return 0

    def on_orientation_changed(self, _checked):
        """Horizontal/Vertical only changes how a *preset ratio* is interpreted
        (current_target_ratio() flips it) - with no preset engaged ("None"),
        orientation has nothing to reinterpret, so this must NOT fall through
        to on_aspect_ratio_selected(), which would unconditionally recompute
        the crop as (0,0,0,0) and destroy any freeform/manual crop in
        progress. With a preset engaged, re-apply it under the new
        orientation, same as picking it fresh from the dropdown."""
        if not self.path or self.aspect_combo.currentText() == "None":
            return
        self.on_aspect_ratio_selected(self.aspect_combo.currentIndex())

    def on_aspect_ratio_selected(self, _index):
        if not self.path:
            return
        chosen_index = self.aspect_combo.currentIndex()
        ratio = self.current_target_ratio()
        if ratio is None:
            left = top = right = bottom = 0
        else:
            vw, vh = self.video_width, self.video_height
            if vw / vh > ratio:
                crop_w = vw - round(vh * ratio)
                crop_h = 0
            else:
                crop_w = 0
                crop_h = vh - round(vw / ratio)
            left, right = crop_w // 2, crop_w - crop_w // 2
            top, bottom = crop_h // 2, crop_h - crop_h // 2
        # show_crop() blanks zero-valued edges (matching every other display path -
        # select_cut, per-cut-crop toggle) instead of writing a literal "0", and
        # re-syncs the Preset dropdown + guide-lock from the crop itself.
        self.show_crop((left, top, right, bottom))
        self.set_current_crop(left, top, right, bottom)
        if ratio is not None:
            # show_crop()'s own reverse-inference (match_aspect_preset) treats an
            # all-zero crop as "None" by default, to avoid falsely engaging a lock
            # for cuts that were never touched - but this preset was just
            # deliberately picked, so it must stick even when applying it produces
            # a (0,0,0,0) crop (the video already matches this ratio exactly).
            self.aspect_combo.setCurrentIndex(chosen_index)
            self.preview.set_locked_ratio(ratio)
            # show_crop() already called set_crop_pixels() above, but with
            # locked_ratio still None at that point (match_aspect_preset just
            # reset the combo to "None" for this all-zero crop) - it computed
            # guide_active as all-False. Now that locked_ratio is restored,
            # redo it so all four guides render as engaged and the corner
            # handles appear, letting a corner drag scale down within this
            # ratio right away instead of the preset silently doing nothing.
            self.preview.set_crop_pixels(left, top, right, bottom)

    def break_aspect_lock(self):
        """Drop the Preset dropdown back to None. Called whenever an edit went
        through unlocked (Lock Aspect Ratio was off, or the box was empty), so a preset
        that no longer matches the actual box stops being displayed as selected."""
        if self.aspect_combo.currentIndex() != 0:
            self.aspect_combo.setCurrentIndex(0)
            self.preview.set_locked_ratio(None)

    def on_guides_changed(self, left, top, right, bottom, preserves_ratio):
        self.set_current_crop(left, top, right, bottom)
        if not preserves_ratio:
            self.break_aspect_lock()
        if self._syncing:
            return
        self._syncing = True
        self.crop_left.setText(str(left))
        self.crop_top.setText(str(top))
        self.crop_right.setText(str(right))
        self.crop_bottom.setText(str(bottom))
        self._syncing = False
        self.update_suffix_placeholder()
        self.update_crop_size_fields()

    def update_crop_size_fields(self):
        """Keep the Size and Position fields showing the crop's current pixel dimensions
        and top-left corner - blank (Size shows the source size as a placeholder,
        Position shows 0,0) when there's no crop at all."""
        if not self.path:
            return
        try:
            left, top, crop_w, crop_h, has_crop = self.get_crop()
        except ValueError:
            return
        was_syncing = self._syncing
        self._syncing = True
        if has_crop:
            self.crop_width_edit.setText(str(crop_w))
            self.crop_height_edit.setText(str(crop_h))
            self.crop_x_edit.setText(str(left))
            self.crop_y_edit.setText(str(top))
        else:
            self.crop_width_edit.clear()
            self.crop_height_edit.clear()
            self.crop_x_edit.clear()
            self.crop_y_edit.clear()
        self._syncing = was_syncing
        # Longest side's default tracks the crop region, not just the
        # source - keep it in sync every time the crop does.
        self.update_scale_default()

    # --- trim fields <-> scrubber sync ---

