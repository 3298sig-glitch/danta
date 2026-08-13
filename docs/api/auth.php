<?php
/**
 * 수익 관리 화면은 공개 사이트에 그대로 노출되므로, PIN으로 세션 인증을 건다.
 * 완벽한 보안은 아니지만(은행 수준 아님), 개인용 도구에 URL만 아는 제3자가
 * 접근하는 걸 막는 최소한의 장치다.
 */

require_once __DIR__ . '/config.php';

function require_auth(): void
{
    if (session_status() !== PHP_SESSION_ACTIVE) {
        session_start();
    }
    if (empty($_SESSION['profit_authed'])) {
        http_response_code(401);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(['error' => '인증이 필요합니다.'], JSON_UNESCAPED_UNICODE);
        exit;
    }
}
