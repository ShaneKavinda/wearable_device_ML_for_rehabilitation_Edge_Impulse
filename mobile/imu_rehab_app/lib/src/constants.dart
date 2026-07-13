class ModelConstants {
  const ModelConstants._();

  static const sampleCount = 33;
  static const axesPerSample = 6;
  static const featureCount = sampleCount * axesPerSample;
  static const frequencyHz = 16.5;
  static const sampleIntervalMs = 60.60606060606061;

  static const axes = <String>[
    'acc_x',
    'acc_y',
    'acc_z',
    'gyro_x',
    'gyro_y',
    'gyro_z',
  ];

  static const labels = <String>[
    'Extension',
    'Flexion',
    'Pronation',
    'Radial Deviation',
    'Supination',
    'Ulnar Deviation',
  ];
}
