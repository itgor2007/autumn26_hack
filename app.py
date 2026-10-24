import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from data_prep import load_statement, get_company, to_daily, data_summary
from categorizer_ml import categorize
from regular_payments import find_regular
from forecaster import forecast_flows
from cashflow_engine import build_forecast, find_gaps
from categories import TRANSFERS
import evaluation

st.set_page_config(page_title='Прогноз денег', layout='wide')

# пока прогноз и поиск разрывов - заглушки, не показываем их
FORECAST_READY = False


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

ops = categorize(ops)
events = find_regular(ops)
flows = forecast_flows(daily, ops, events)
forecast = build_forecast(summary['last_balance'], summary['last_date'], flows, events)
alerts = find_gaps(forecast, events)

tab_forecast, tab_quality = st.tabs(['Прогноз', 'Качество прогноза'])

# =====================================================================
with tab_forecast:
    st.title('Прогноз остатка на 30 дней')

    col1, col2, col3 = st.columns(3)
    col1.metric('Остаток сейчас', money(summary['last_balance']))
    col2.metric('История', f"{summary['days']} дн.")
    col3.metric('Операций', summary['operations'])

    if summary['short_history']:
        st.warning('История меньше 3 месяцев, прогноз будет неточным')
    if summary['gaps'] > 0:
        st.warning(f"В выписке есть пропуски: {summary['gaps']} шт., всего {summary['gap_days']} дн.")

    if FORECAST_READY:
        if alerts:
            for alert in alerts:
                st.error(f"{alert['date']:%d.%m.%Y}: может не хватить {money(alert['shortfall'])}")
        else:
            st.success('Кассовых разрывов в ближайшие 30 дней не видно')

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily['date'], y=daily['balance'], name='История'))
    if FORECAST_READY:
        fig.add_trace(go.Scatter(x=forecast['date'], y=forecast['balance'], name='Прогноз',
                                 line=dict(dash='dash')))
    fig.add_hline(y=0, line_color='red', line_dash='dot')
    fig.update_layout(height=450, yaxis_title='₽')
    st.plotly_chart(fig, width='stretch')

    # ---------- ближайшие платежи ----------
    start = summary['last_date'] + pd.Timedelta(days=1)
    end = start + pd.Timedelta(days=29)
    st.header('Ближайшие обязательные платежи')
    st.caption(f"С {start:%d.%m.%Y} по {end:%d.%m.%Y}. Найдены по истории операций и налоговому календарю")

    if events.empty:
        st.info('Регулярных платежей в истории не нашли')
    else:
        st.metric('Всего к оплате за 30 дней', money(-events['amount'].sum()))
        sources = {'regular': 'найден в истории', 'tax': 'налоговый календарь', 'manual': 'введён вручную'}
        table = pd.DataFrame({
            'Дата': events['date'].dt.strftime('%d.%m.%Y'),
            'Платёж': events['name'],
            'Сумма, ₽': (-events['amount']).round(0),
            'Откуда известно': events['source'].map(sources),
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
