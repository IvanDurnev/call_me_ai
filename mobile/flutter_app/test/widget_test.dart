import 'package:flutter_test/flutter_test.dart';

import 'package:call_me_ai_mobile/app/app.dart';

void main() {
  testWidgets('app bootstrap renders', (WidgetTester tester) async {
    await tester.pumpWidget(const CallMeAiApp());

    expect(find.byType(CallMeAiApp), findsOneWidget);
  });
}
