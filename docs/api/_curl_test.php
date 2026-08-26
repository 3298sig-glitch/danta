<?php
/**
 * 진단용 임시 스크립트: 닷홈 무료 호스팅 PHP가 외부(네이버) HTTPS로 아웃바운드
 * 요청을 보낼 수 있는지 확인한다. "오늘의 시황" 프록시(api/market_summary.php)를
 * 만들지 결정하기 위한 1회성 테스트 - 확인 끝나면 이 파일은 삭제한다.
 */

header('Content-Type: application/json; charset=utf-8');

$url = 'https://m.stock.naver.com/api/index/KOSPI/basic';
$result = [
    'curl_available' => function_exists('curl_init'),
    'allow_url_fopen' => ini_get('allow_url_fopen') ? true : false,
];

if (function_exists('curl_init')) {
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36');
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);
    $body = curl_exec($ch);
    $result['curl_http_code'] = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $result['curl_error'] = curl_error($ch) ?: null;
    $result['curl_body_snippet'] = $body !== false ? substr($body, 0, 200) : null;
    curl_close($ch);
}

if (ini_get('allow_url_fopen')) {
    $ctx = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n",
            'timeout' => 10,
        ],
    ]);
    $fopen_body = @file_get_contents($url, false, $ctx);
    $result['fopen_success'] = $fopen_body !== false;
    $result['fopen_body_snippet'] = $fopen_body !== false ? substr($fopen_body, 0, 200) : null;
    $result['fopen_last_error'] = error_get_last()['message'] ?? null;
}

echo json_encode($result, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);
