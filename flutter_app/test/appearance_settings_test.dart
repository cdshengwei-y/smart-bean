import 'package:ai_recording_bean/appearance_settings.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('uses forest green for missing or unknown saved accents', () {
    expect(AppearanceAccent.fromStorage(null), AppearanceAccent.forest);
    expect(
      AppearanceAccent.fromStorage('unknown-accent'),
      AppearanceAccent.forest,
    );
  });

  test('restores every supported saved accent', () {
    for (final accent in AppearanceAccent.values) {
      expect(AppearanceAccent.fromStorage(accent.storageValue), accent);
    }
  });
}
