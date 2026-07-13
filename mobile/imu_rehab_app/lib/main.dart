import 'package:flutter/material.dart';

import 'src/ui/rehab_dashboard_page.dart';

void main() {
  runApp(const ImuRehabApp());
}

class ImuRehabApp extends StatelessWidget {
  const ImuRehabApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'ESP32 IMU Inference',
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF0F766E),
          brightness: Brightness.light,
        ),
        scaffoldBackgroundColor: const Color(0xFFF6F7F9),
        visualDensity: VisualDensity.standard,
      ),
      home: const RehabDashboardPage(),
    );
  }
}
