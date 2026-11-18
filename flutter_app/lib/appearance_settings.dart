import 'package:flutter/material.dart';

enum AppearanceAccent {
  forest('forest', '森林绿', Color(0xFF176B58)),
  teal('teal', '青绿色', Color(0xFF087E7A)),
  coral('coral', '暖珊瑚', Color(0xFFC45A43));

  const AppearanceAccent(this.storageValue, this.label, this.color);

  final String storageValue;
  final String label;
  final Color color;

  static AppearanceAccent fromStorage(String? value) {
    return AppearanceAccent.values.firstWhere(
      (accent) => accent.storageValue == value,
      orElse: () => AppearanceAccent.forest,
    );
  }
}
