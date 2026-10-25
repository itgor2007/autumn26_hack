import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from data_prep import load_statement, get_company, to_daily, data_summary
from categorizer_ml import categorize
from regular_payments import find_regular, add_manual_event
from forecaster import forecast_flows, weekday_profile, WEEKS
from cashflow_engine import build_forecast, find_gaps, GAP_PROB
from categories import TRANSFERS, OTHER
import evaluation

st.set_page_config(page_title='Прогноз денег', layout='wide')

WEEKDAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
SOURCES = {'regular': 'найден в истории', 'tax': 'налоговый календарь', 'manual': 'введён вручную'}


@st.cache_data
def load_dataset():
    return load_statement('data/all_operations.csv')


def money(x):
    return f'{x:,.0f} ₽'.replace(',', ' ')


st.sidebar.title('Данные')
source = st.sidebar.radio('Откуда взять выписку', ['Пример из датасета', 'Загрузить свой CSV'])

if source == 'Загрузить свой CSV':
    file = st.sidebar.file_uploader('Выписка (CSV, разделитель ;)', type='csv')
    if file is None:
        st.info('Загрузите выписку слева')
        st.stop()
    try:
        all_ops, problems = load_statement(file)
    except ValueError as e:
        st.error(str(e))
        st.stop()
    data_name = file.name
else:
    all_ops, problems = load_dataset()
    data_name = 'датасет'

company_id = st.sidebar.selectbox('Компания', sorted(all_ops['company_id'].unique()))
ops = get_company(all_ops, company_id)
daily = to_daily(ops)
summary = data_summary(ops, daily)
start = summary['last_date'] + pd.Timedelta(days=1)
end = start + pd.Timedelta(days=29)

# ---------- ручной ввод событий ----------
# храним по компаниям, чтобы при переключении компании события не перемешивались
manual = st.session_state.setdefault('manual', {}).setdefault(company_id, [])

st.sidebar.divider()
st.sidebar.subheader('Добавить событие в прогноз')
st.sidebar.caption('Например: "клиент обещал заплатить 300 000 ₽ 20-го" или "куплю оборудование"')
with st.sidebar.form('manual_form', clear_on_submit=True):
    m_date = st.date_input('Дата', value=start.date(), min_value=start.date(), max_value=end.date(),
                           format='DD.MM.YYYY')
    m_kind = st.radio('Что это', ['Списание', 'Поступление'], horizontal=True)
    m_amount = st.number_input('Сумма, ₽', min_value=0.0, step=10000.0, format='%.0f')
    m_name = st.text_input('Описание', placeholder='Оплата от клиента')
    if st.form_submit_button('Добавить в прогноз'):
        if m_amount > 0:
            sign = -1 if m_kind == 'Списание' else 1
            manual.append({'date': m_date, 'amount': sign * m_amount,
                           'name': m_name.strip() or m_kind})
        else:
            st.warning('Сумма должна быть больше нуля')

if manual:
    st.sidebar.markdown('**Добавлено вручную:**')
    for m in manual:
        st.sidebar.write(f"{m['date']:%d.%m} — {m['name']}: {'+' if m['amount'] > 0 else '−'}{money(abs(m['amount']))}")
    if st.sidebar.button('Удалить все свои события'):
        manual.clear()
        st.rerun()

# ---------- расчёт ----------
ops = categorize(ops)
events = find_regular(ops)
for m in manual:
    events = add_manual_event(events, m['date'], m['amount'], m['name'], OTHER)
flows = forecast_flows(daily, ops, events)
forecast = build_forecast(summary['last_balance'], summary['last_date'], flows, events)
alerts = find_gaps(forecast, events)

tab_forecast, tab_log, tab_quality = st.tabs(['Прогноз', 'Как посчитан прогноз', 'Качество прогноза'])

# =====================================================================
with tab_forecast:
    st.title('Прогноз остатка на 30 дней')

    lowest = forecast.loc[forecast['balance'].idxmin()]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric('Остаток сейчас', money(summary['last_balance']))
    col2.metric(f"Минимум по прогнозу ({lowest['date']:%d.%m})", money(lowest['balance']))
    col3.metric('Вероятность разрыва', f"{forecast['prob'].max():.0%}")
    col4.metric('История', f"{summary['days']} дн.", help=f"Операций: {summary['operations']}")

    if summary['short_history']:
        st.warning('История меньше 3 месяцев, прогноз будет неточным')
    if summary['gaps'] > 0:
        st.warning(f"В выписке есть пропуски: {summary['gaps']} шт., всего {summary['gap_days']} дн. "
                   'Эти дни не используем для прогноза')

    if alerts:
        alert = alerts[0]
        st.error(f"### Риск кассового разрыва с {alert['date']:%d.%m.%Y}\n"
                 f"К **{alert['deepest_date']:%d.%m.%Y}** может не хватить **{money(alert['shortfall'])}**. "
                 f"Вероятность: **{alert['probability']:.0%}**")
        col_why, col_todo = st.columns(2)
        with col_why:
            st.markdown('**Почему**')
            for r in alert['reasons']:
                st.markdown(f'- {r}')
        with col_todo:
            st.markdown('**Что можно сделать**')
            for a in alert['advice']:
                st.markdown(f'- {a}')
    else:
        st.success(f"Кассовых разрывов в ближайшие 30 дней не видно. Самый низкий остаток по прогнозу — "
                   f"{money(lowest['balance'])} ({lowest['date']:%d.%m})")

    show_all = st.checkbox('Показать всю историю', value=False)
    hist = daily if show_all else daily[daily['date'] > summary['last_date'] - pd.Timedelta(days=120)]

    # прогноз рисуем от последнего известного остатка, чтобы линия не обрывалась
    first = pd.DataFrame({'date': [summary['last_date']], 'balance': [summary['last_balance']],
                          'low': [summary['last_balance']], 'high': [summary['last_balance']]})
    line = pd.concat([first, forecast[['date', 'balance', 'low', 'high']]], ignore_index=True)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist['date'], y=hist['balance'], name='История', line=dict(color='#4c8bf5')))
    # коридор: с вероятностью ~90% остаток будет внутри
    fig.add_trace(go.Scatter(x=line['date'], y=line['high'], line=dict(width=0),
                             showlegend=False, hoverinfo='skip'))
    fig.add_trace(go.Scatter(x=line['date'], y=line['low'], line=dict(width=0), fill='tonexty',
                             fillcolor='rgba(255,165,0,0.2)', name='Коридор 90%', hoverinfo='skip'))
    fig.add_trace(go.Scatter(x=line['date'], y=line['balance'], name='Прогноз',
                             line=dict(color='orange', dash='dash')))
    pays = events.assign(date=pd.to_datetime(events['date']))
    pays = pays[pays['amount'] < 0].groupby('date')['name'].apply(lambda x: '<br>'.join(x))
    marks = forecast[forecast['date'].isin(pays.index)]
    fig.add_trace(go.Scatter(x=marks['date'], y=marks['balance'], mode='markers', name='Обязательные платежи',
                             marker=dict(color='orange', size=8), text=marks['date'].map(pays),
                             hovertemplate='%{x|%d.%m}<br>%{text}<extra></extra>'))
    fig.add_hline(y=0, line_color='red', line_dash='dot')
    if alerts:
        fig.add_vline(x=alerts[0]['deepest_date'], line_color='red', line_dash='dash')
    fig.update_layout(height=450, yaxis_title='₽', hovermode='x unified')
    st.plotly_chart(fig, width='stretch')

    # ---------- ближайшие платежи ----------
    st.header('Ближайшие платежи и поступления')
    st.caption(f"С {start:%d.%m.%Y} по {end:%d.%m.%Y}. Найдены по истории операций, "
               'налоговому календарю и добавлены вручную')

    if events.empty:
        st.info('Регулярных платежей в истории не нашли')
    else:
        st.metric('Обязательные платежи за 30 дней', money(-events.loc[events['amount'] < 0, 'amount'].sum()))
        table = pd.DataFrame({
            'Дата': pd.to_datetime(events['date']).dt.strftime('%d.%m.%Y'),
            'Платёж': events['name'],
            'Сумма, ₽': events['amount'].round(0),
            'Откуда известно': events['source'].map(SOURCES),
        })
        st.dataframe(table, width='stretch', hide_index=True)

    # ---------- категории ----------
    st.header('Куда приходят и уходят деньги')

    rules_share = (ops['category_source'] == 'rules').mean() * 100
    st.caption(f'Категория определена для {rules_share:.1f}% операций')

    def category_chart(part, title):
        by_cat = part.groupby('category')['amount'].sum().abs().sort_values()
        fig = go.Figure(go.Bar(x=by_cat.values, y=by_cat.index, orientation='h'))
        fig.update_layout(title=title, height=450, xaxis_title='₽', margin=dict(l=10))
        return fig

    # переводы между своими счетами - это не доход и не расход, на графиках их не показываем
    real = ops[ops['category'] != TRANSFERS]
    col_in, col_out = st.columns(2)
    col_in.plotly_chart(category_chart(real[real['amount'] > 0], 'Поступления'), width='stretch')
    col_out.plotly_chart(category_chart(real[real['amount'] < 0], 'Списания'), width='stretch')
    st.caption('Переводы между своими счетами в другие банки не показаны: это не доход и не расход')

    with st.expander('Операции по категориям'):
        chosen = st.selectbox('Категория', ['Все'] + sorted(ops['category'].unique()))
        table = ops if chosen == 'Все' else ops[ops['category'] == chosen]
        st.dataframe(table[['date', 'amount', 'counterparty', 'purpose', 'category']],
                     width='stretch', hide_index=True)

    with st.expander('Проблемы в данных'):
        for p in problems:
            st.write('-', p)

# =====================================================================
with tab_log:
    st.title('Как посчитан прогноз')
    st.caption(f'Компания {company_id}. Все числа ниже - те же, что использует вкладка "Прогноз"')

    st.subheader('Шаг 1. Загрузка и чистка выписки')
    st.markdown(
        f"- История: с {summary['first_date']:%d.%m.%Y} по {summary['last_date']:%d.%m.%Y} "
        f"({summary['days']} дн.), операций: {summary['operations']}\n"
        f"- Пропусков в выписке: {summary['gaps']} (всего {summary['gap_days']} дн.) - "
        'найдены по остатку: вчерашний остаток + приход - расход не равен сегодняшнему\n'
        f"- Остаток на {summary['last_date']:%d.%m.%Y}: **{money(summary['last_balance'])}** - "
        'с него начинается прогноз'
    )
    for p in problems:
        st.markdown(f'- {p} (по всему загруженному файлу)')

    st.subheader('Шаг 2. Категории операций')
    st.markdown(f'Правила по назначению платежа и контрагенту разметили {rules_share:.1f}% операций.')
    by_cat = ops.groupby('category')['amount'].agg(['count', 'sum']).sort_values('sum')
    by_cat.columns = ['Операций', 'Сумма, ₽']
    st.dataframe(by_cat.round(0), width='stretch')

    st.subheader('Шаг 3. Известные будущие платежи')
    n_by_source = events['source'].map(SOURCES).value_counts()
    st.markdown(
        f"Найдено событий на 30 дней: **{len(events)}** "
        f"({', '.join(f'{k}: {v}' for k, v in n_by_source.items()) or 'нет'}). "
        'Регулярные - зарплата, налоги, аренда и т.д.: платёж повторяется минимум 3 раза с ровным интервалом '
        'и похожей суммой. Сумма - медиана последних платежей. ЕНП - 28-го числа (с переносом с выходных).'
    )

    st.subheader('Шаг 4. Прогноз остальных денег (выручка, закупки, услуги...)')
    by_weekday, sigma, used = weekday_profile(daily, ops)
    st.markdown(
        f'Берём операции, которые не попали в регулярные, без "спасательных" поступлений '
        f'(владелец докладывает деньги, кредиты). Считаем средний поток по каждому дню недели '
        f'за последние {WEEKS} недель: использовано **{len(used)} дн.** (дни с пропусками в выписке не берём).'
    )
    profile = pd.DataFrame({'День недели': WEEKDAYS,
                            'Средний поток, ₽': [round(by_weekday.get(i, 0)) for i in range(7)]})
    st.dataframe(profile.set_index('День недели').T, width='stretch')
    st.markdown(f'Разброс одного дня (sigma): **{money(sigma)}** - насколько обычно реальный день '
                'отличается от среднего. Нужен для вероятности.')

    st.subheader('Шаг 5. Остаток по дням')
    st.markdown(
        '- **Остаток** = остаток вчера + прогноз потока за день + известные платежи за день\n'
        '- **Коридор** = остаток ± 1.65 · sigma · √(номер дня) - в нём остаток окажется с вероятностью ~90%. '
        'Неопределённость растёт с каждым днём, потому что ошибки дней складываются\n'
        '- **Вероятность минуса** = вероятность, что реальный остаток с таким разбросом окажется ниже нуля '
        '(нормальное распределение)'
    )
    ev_by_day = events.assign(date=pd.to_datetime(events['date'])).groupby('date')
    log = pd.DataFrame({
        'Дата': forecast['date'].dt.strftime('%d.%m.%Y'),
        'День': forecast['date'].dt.day_of_week.map(dict(enumerate(WEEKDAYS))),
        'Поток (прогноз), ₽': flows['flow'].round(0).values,
        'Платежи, ₽': forecast['date'].map(ev_by_day['amount'].sum()).fillna(0).round(0).values,
        'Что за платежи': forecast['date'].map(ev_by_day['name'].apply(', '.join)).fillna('').values,
        'Остаток, ₽': forecast['balance'].round(0).values,
        'Нижняя граница, ₽': forecast['low'].round(0).values,
        'Вероятность минуса': (forecast['prob'] * 100).round(1).astype(str).values + '%',
    })
    st.dataframe(log, width='stretch', hide_index=True)
    st.download_button('Скачать расчёт по дням (CSV)', log.to_csv(index=False, sep=';').encode('utf-8-sig'),
                       f'forecast_{company_id}.csv', 'text/csv')

    st.subheader('Шаг 6. Вывод')
    if alerts:
        alert = alerts[0]
        st.markdown(
            f"Порог предупреждения - вероятность {GAP_PROB:.0%} (на бэктесте даёт лучший F1). "
            f"Первый день, где вероятность минуса не меньше порога: **{alert['date']:%d.%m.%Y}**. "
            f"Самая глубокая точка: **{alert['deepest_date']:%d.%m.%Y}**, может не хватить "
            f"**{money(alert['shortfall'])}**. Максимальная вероятность: **{alert['probability']:.0%}**."
        )
    else:
        st.markdown(f"Ни в один из 30 дней вероятность минуса не достигла порога {GAP_PROB:.0%} - "
                    'предупреждения нет.')

# =====================================================================
with tab_quality:
    st.title('Качество прогноза')
    st.markdown(
        'Проверяем прогноз на прошлом (бэктест): для каждой компании "отматываем время" '
        f'через каждые {evaluation.STEP_DAYS} дней, строим прогноз на {evaluation.HORIZON} дней '
        'так же, как во вкладке "Прогноз", и сравниваем с тем, что было на самом деле.\n\n'
        '**Кассовый разрыв** - остаток ушёл бы в минус, если бы владелец не доложил свои деньги '
        'и компания не взяла кредит. Именно о таких ситуациях сервис должен предупреждать заранее.'
    )

    scope = st.radio('На чём проверять', ['Все компании из загруженных данных', 'Только выбранная компания'],
                     horizontal=True)
    if scope.startswith('Все'):
        st.caption(f"Компаний: {all_ops['company_id'].nunique()}. На 150 компаниях считается около 2 минут")

    key = (data_name, scope, company_id if scope.startswith('Только') else None)
    if st.button('Посчитать метрики', type='primary'):
        test_ops = all_ops if scope.startswith('Все') else get_company(all_ops, company_id)
        bar = st.progress(0.0, text='Размечаем категории...')
        test_ops = categorize(test_ops)
        results = evaluation.run_backtest(
            test_ops, progress=lambda share, c: bar.progress(share, text=f'Проверяем {c}...'))
        bar.empty()
        st.session_state['backtest'] = (key, results)

    saved = st.session_state.get('backtest')
    if saved is None or saved[0] != key:
        st.info('Нажмите "Посчитать метрики"')
    elif saved[1].empty:
        st.warning('Не хватает истории для проверки: нужно хотя бы 4 месяца операций')
    else:
        results = saved[1]
        threshold = st.slider('Порог вероятности для предупреждения', 0.1, 0.9, evaluation.THRESHOLD, 0.05)
        m, matrix = evaluation.calc_metrics(results, threshold)

        st.caption(f"Прогнозов: {m['forecasts']}, компаний: {m['companies']}, "
                   f"реальных разрывов: {m['real_gaps']}")
        if m['real_gaps'] == 0:
            st.info('В этих данных за всё время не было ни одного разрыва, поэтому precision, recall и F1 '
                    'посчитать не на чем. Смотрите accuracy (сколько раз верно сказали "разрыва не будет") '
                    'и ошибку прогноза остатка')

        st.subheader('Предупреждения о разрывах')
        c = st.columns(5)
        c[0].metric('Accuracy', f"{m['accuracy']:.2f}")
        c[1].metric('Precision', f"{m['precision']:.2f}")
        c[2].metric('Recall', f"{m['recall']:.2f}")
        c[3].metric('F1', f"{m['f1']:.2f}")
        c[4].metric('ROC AUC', '—' if m['roc_auc'] is None else f"{m['roc_auc']:.2f}")
        if m['days_ahead'] is not None:
            st.write(f"Пойманные разрывы мы видим в среднем **за {m['days_ahead']:.0f} дн.** до того, как они случаются")

        col_matrix, col_th = st.columns(2)
        with col_matrix:
            labels = ['Разрыв был', 'Разрыва не было']
            heat = go.Figure(go.Heatmap(
                z=matrix, x=['Предсказали', 'Не предсказали'], y=labels,
                text=matrix, texttemplate='%{text}', textfont=dict(size=18),
                colorscale='Blues', showscale=False))
            heat.update_layout(title='Матрица ошибок', height=380, yaxis_autorange='reversed',
                               xaxis=dict(side='top', tickangle=0))
            st.plotly_chart(heat, width='stretch')
        with col_th:
            st.markdown('**Как метрики зависят от порога**')
            st.dataframe(evaluation.threshold_table(results).round(3), hide_index=True, width='stretch')
            st.caption('Чем ниже порог, тем больше разрывов ловим (recall), но больше ложных тревог')

        st.subheader('Ошибка прогноза остатка')
        c = st.columns(3)
        c[0].metric('MAE', money(m['mae']))
        c[1].metric('RMSE', money(m['rmse']))
        c[2].metric('MSE', f"{m['mse']:.3e}")

        st.markdown('**Сравнение подходов**')
        compare = pd.DataFrame({
            'Подход': ['Остаток не изменится', 'Наш прогноз'],
            'MAE, ₽': [round(m['mae_naive']), round(m['mae'])],
            'RMSE, ₽': [round(m['rmse_naive']), round(m['rmse'])],
            'Recall': [0.0, round(m['recall'], 3)],
            'F1': [0.0, round(m['f1'], 3)],
        })
        st.dataframe(compare, hide_index=True, width='stretch')
        st.caption('Прогноз "остаток не изменится" по ошибке в рублях близок к нашему, '
                   'но не может предсказать ни одного разрыва')

        st.subheader('Прогноз и факт на конкретном примере')
        results = results.reset_index(drop=True)
        names = (results['company_id'] + ' с ' + results['cut_date'].dt.strftime('%d.%m.%Y')
                 + results['real_gap'].map({True: ' (был разрыв)', False: ''}))
        # по умолчанию показываем пример, где разрыв был, мы его поймали и прогноз сам ушёл в минус
        caught = results[results['real_gap'] & (results['prob'] >= threshold) & (results['pred_min'] < 0)]
        default = int(caught['mae'].idxmin()) if len(caught) else 0
        row_id = st.selectbox('Прогноз', results.index, index=default, format_func=lambda i: names[i])
        row = results.loc[row_id]
        days = pd.date_range(row['cut_date'], periods=len(row['pred']))
        ex = go.Figure()
        ex.add_trace(go.Scatter(x=days, y=row['real'], name='Факт (без экстренных пополнений)'))
        ex.add_trace(go.Scatter(x=days, y=row['pred'], name='Прогноз', line=dict(dash='dash')))
        ex.add_hline(y=0, line_color='red', line_dash='dot')
        ex.update_layout(height=400, yaxis_title='₽')
        st.plotly_chart(ex, width='stretch')
        st.write(f"Вероятность разрыва по прогнозу: **{row['prob']:.0%}**")

        csv = results.drop(columns=['pred', 'real']).to_csv(index=False, sep=';').encode('utf-8-sig')
        st.download_button('Скачать все прогнозы (CSV)', csv, 'backtest.csv', 'text/csv')
